"""Healthcare router agent: decides between Genie, Unity Catalog functions and policy documents.

    question ─► guard_and_route ─┬─► genie ─────────┐
                                 ├─► uc_functions ──┼─► check ─┬─► synthesize ─► answer
                                 ├─► documents ─────┘          └─► (fallback route) ─► ...
                                 └─► guardrail_reply ─► answer

* guard_and_route: guardrails, follow-up rewriting, then rules (routing.py) and, if they
  aren't confident, an LLM classifier with structured output. One question can fan out
  to several routes in parallel (e.g. a claim lookup plus the appeals policy).
* check: if every route failed or came back empty, try the next route in FALLBACKS.
* synthesize: one result passes through; several are merged by the LLM.

Logged with MLflow "models from code" and served as a ResponsesAgent.
"""
from __future__ import annotations

import json
import operator
import os
from collections.abc import Generator
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

import mlflow
import routing as R
from databricks_langchain import ChatDatabricks, DatabricksVectorSearch

from databricks_langchain.genie import GenieAgent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from mlflow.models import ModelConfig
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent
from pydantic import BaseModel, Field
from unitycatalog.ai.core.databricks import DatabricksFunctionClient
from unitycatalog.ai.langchain.toolkit import UCFunctionToolkit

mlflow.langchain.autolog()

# --------------------------------------------------------------------------- prompts

ROUTER_PROMPT = """You route questions for a health network's internal assistant used by staff.
Pick one or more routes:
- "genie": aggregate or numeric questions over de-identified data (counts, rates, averages, trends,
  rankings, comparisons by month/facility/payer/diagnosis). Example: readmission rate by facility.
- "uc_functions": lookups about ONE specific entity using an identifier or exact codes: a patient ID
  (PAT-...), a claim ID (CLM-...), prior-auth for a CPT code + payer, formulary status of a drug for a
  payer, a drug-drug interaction, a patient's readmission risk.
- "documents": policies, protocols, processes, definitions, eligibility criteria, deadlines, "how do we..." questions.
Use two routes only when the question clearly needs both (e.g. "claim CLM-0000123 was denied, how do we appeal?"
needs uc_functions + documents). Available routes: {available}.
Rule-based hints (may be wrong): scores={scores}; entities={entities}."""

CONDENSE_PROMPT = """Rewrite the user's latest message as a standalone question, using the conversation
for missing context (IDs, payers, time periods). Return only the question.

Conversation:
{history}

Latest message: {question}"""

UC_PROMPT = """You answer lookup questions with Unity Catalog functions. Call the functions you need
with exact identifiers, then answer in 2-5 sentences using only what the functions returned.
Payer IDs: PAY-01 Summit Health PPO, PAY-02 Summit Health HMO, PAY-03 Bluepoint Medicare Advantage,
PAY-04 Commonwealth Medicaid, PAY-05 Self-pay. Identifiers found in the question: {entities}.
If a function says the record was not found, say so plainly. Never invent values.
Drug interaction results are a demo reference, not clinical advice; say so."""

DOCS_PROMPT = """Answer the staff member's question using ONLY the policy excerpts below.
Cite each fact with the bracketed source label, e.g. [Prior Authorization Policy — Turnaround times].
Be concise and specific (numbers, deadlines, steps). If the excerpts do not contain the answer,
reply with exactly: NOT_IN_DOCS

Excerpts:
{context}"""

SYNTH_PROMPT = """Combine these partial answers into one clear answer to the question. Keep every
number, identifier, deadline and citation; don't add facts. Lead with the direct answer, then any
policy guidance. Question: {question}

{parts}"""

ROUTE_LABELS = {R.GENIE: "Data (Genie)", R.UC: "Record lookup (UC functions)", R.DOCS: "Policy documents"}


class RouterOutput(BaseModel):
    routes: list[Literal["genie", "uc_functions", "documents"]] = Field(min_length=1, max_length=3)
    reason: str = Field(description="One short sentence explaining the choice")


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class AgentState(TypedDict, total=False):
    question: str
    history: list[dict]
    standalone_question: str
    decision: dict
    pending: list[str]
    routes_tried: Annotated[list[str], operator.add]
    results: Annotated[dict, _merge]
    answer: str
    genie_conversation_id: str | None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content or "")


# --------------------------------------------------------------------------- graph

def build_graph(cfg: dict):
    llm = ChatDatabricks(endpoint=cfg["llm_endpoint"], temperature=0)
    available = tuple(r for r in R.ROUTES if r != R.GENIE or cfg.get("genie_space_id"))
    uc_tools = UCFunctionToolkit(function_names=list(cfg["uc_function_names"]),
                                 client=DatabricksFunctionClient()).tools
    tools_by_name = {t.name: t for t in uc_tools}
    vector_store = DatabricksVectorSearch(
    index_name=cfg["vector_search_index"],
    columns=["chunk_id", "doc_title", "section", "content"],
    )
    num_docs = int(cfg.get("num_doc_results", 4))
    genie = (
        GenieAgent(genie_space_id=cfg["genie_space_id"], genie_agent_name="Genie",
                   description=cfg.get("genie_description", ""))
        if cfg.get("genie_space_id") else None
    )

    # ---- nodes
    def guard_and_route(state: AgentState) -> dict:
        q = state["question"]
        guard = R.check_guardrails(q)
        if guard:
            return {"decision": {"routes": [], "source": "guardrail", "guardrail": guard,
                                 "reason": f"guardrail: {guard}", "confidence": 1.0},
                    "pending": [], "standalone_question": q}

        if state.get("history"):
            hist = "\n".join(f"{m['role']}: {m['content']}" for m in state["history"][-6:])
            q = _text(llm.invoke(CONDENSE_PROMPT.format(history=hist, question=q)).content).strip() or q

        forced = state.get("decision", {}).get("forced")
        if forced:
            decision = R.RouteDecision([r for r in forced if r in available], "forced by caller", "forced", 1.0)
        else:
            decision = R.rule_route(q, available, float(cfg.get("rule_confidence_threshold", 0.7)))
            mode = cfg.get("routing_mode", "hybrid")
            if mode == "llm" or (mode == "hybrid" and not decision.confident):
                decision = _llm_route(q, decision, available)
        return {"decision": decision.__dict__, "pending": decision.routes, "standalone_question": q}

    def _llm_route(q: str, rules: R.RouteDecision, available) -> R.RouteDecision:
        prompt = ROUTER_PROMPT.format(available=list(available), scores=rules.scores,
                                      entities=R.extract_entities(q).as_hint())
        try:
            out = llm.with_structured_output(RouterOutput).invoke(
                [SystemMessage(prompt), HumanMessage(q)])
            routes = [r for r in dict.fromkeys(out.routes) if r in available]
            if routes:
                return R.RouteDecision(routes, f"llm: {out.reason}", "llm", 0.9, rules.scores, confident=True)
        except Exception as exc:  # structured output unsupported or malformed -> keep rules
            rules.reason += f" | llm router failed: {type(exc).__name__}"
        return rules

    def genie_node(state: AgentState) -> dict:
        q = state["standalone_question"]
        if genie is None:
            return {"results": {R.GENIE: {"ok": False, "error": "Genie route disabled"}}, "routes_tried": [R.GENIE]}
        try:
            payload = {"messages": [{"role": "user", "content": q}]}
            if state.get("genie_conversation_id"):
                payload["conversation_id"] = state["genie_conversation_id"]
            out = genie.invoke(payload)
            msgs = out.get("messages", []) if isinstance(out, dict) else []
            answer = "\n\n".join(_text(m.get("content") if isinstance(m, dict) else m.content) for m in msgs
                                 if (m.get("role") if isinstance(m, dict) else getattr(m, "type", "")) in ("assistant", "ai"))
            ok = bool(answer.strip())
            return {"results": {R.GENIE: {"ok": ok, "answer": answer, "sources": ["Genie space"],
                                          "conversation_id": out.get("conversation_id") if isinstance(out, dict) else None}},
                    "routes_tried": [R.GENIE]}
        except Exception as exc:
            return {"results": {R.GENIE: {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}, "routes_tried": [R.GENIE]}

    def uc_node(state: AgentState) -> dict:
        q = state["standalone_question"]
        ents = R.extract_entities(q).as_hint()
        msgs = [SystemMessage(UC_PROMPT.format(entities=ents)), HumanMessage(q)]
        bound = llm.bind_tools(uc_tools)
        calls: list[str] = []
        try:
            ai = None
            for _ in range(int(cfg.get("max_tool_iterations", 4))):
                ai = bound.invoke(msgs)
                msgs.append(ai)
                if not ai.tool_calls:
                    break
                for call in ai.tool_calls:
                    tool = tools_by_name.get(call["name"])
                    result = tool.invoke(call["args"]) if tool else f"Unknown function {call['name']}"
                    calls.append(call["name"].split("__")[-1])
                    msgs.append(ToolMessage(content=_text(result), tool_call_id=call["id"]))
            answer = _text(ai.content) if isinstance(ai, AIMessage) else ""
            ok = bool(calls) and bool(answer.strip())
            return {"results": {R.UC: {"ok": ok, "answer": answer, "sources": [f"UC function {c}" for c in dict.fromkeys(calls)],
                                       **({} if ok else {"error": "no function call made"})}},
                    "routes_tried": [R.UC]}
        except Exception as exc:
            return {"results": {R.UC: {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}, "routes_tried": [R.UC]}

    def docs_node(state: AgentState) -> dict:
        q = state["standalone_question"]
        try:
            docs = vector_store.similarity_search(q, k=num_docs)
            if not docs:
                return {"results": {R.DOCS: {"ok": False, "error": "no documents retrieved"}}, "routes_tried": [R.DOCS]}
            labels, blocks = [], []
            for d in docs:
                if isinstance(d, str):  # defensive: some library versions return plain text
                    label, text = "Policy documents", d
                else:
                    meta = d.metadata or {}
                    label = f"{meta.get('doc_title', 'Policy')} — {meta.get('section', '')}".strip(" —")
                    text = d.page_content
                labels.append(label)
                blocks.append(f"[{label}]\n{text}")
            answer = _text(llm.invoke([SystemMessage(DOCS_PROMPT.format(context="\n\n".join(blocks))),
                                       HumanMessage(q)]).content).strip()
            ok = bool(answer) and "NOT_IN_DOCS" not in answer
            return {"results": {R.DOCS: {"ok": ok, "answer": answer if ok else "",
                                         "sources": list(dict.fromkeys(labels)),
                                         **({} if ok else {"error": "answer not in retrieved documents"})}},
                    "routes_tried": [R.DOCS]}
        except Exception as exc:
            return {"results": {R.DOCS: {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}, "routes_tried": [R.DOCS]}

    def check(state: AgentState) -> dict:
        results = state.get("results", {})
        if any(r.get("ok") for r in results.values()) or not cfg.get("enable_fallback", True):
            return {"pending": []}
        nxt = R.fallback_routes(list(dict.fromkeys(state.get("routes_tried", []))), available)
        return {"pending": nxt}

    def synthesize(state: AgentState) -> dict:
        results = state.get("results", {})
        good = {k: v for k, v in results.items() if v.get("ok")}
        if not good:
            tried = ", ".join(ROUTE_LABELS[k] for k in results) or "none"
            return {"answer": "I couldn't find an answer to that in the data, record lookups or policy documents "
                              f"(tried: {tried}). Try adding an identifier (patient, claim, CPT code, payer) or rephrasing."}
        if len(good) == 1:
            route, res = next(iter(good.items()))
            text = res["answer"]
        else:
            parts = "\n\n".join(f"## {ROUTE_LABELS[k]}\n{v['answer']}" for k, v in good.items())
            text = _text(llm.invoke(SYNTH_PROMPT.format(question=state["standalone_question"], parts=parts)).content)
        sources = [s for v in good.values() for s in v.get("sources", [])]
        if sources and R.DOCS not in good:  # docs answers already carry inline citations
            text += "\n\nSources: " + "; ".join(dict.fromkeys(sources))
        conv = good.get(R.GENIE, {}).get("conversation_id")
        return {"answer": text, **({"genie_conversation_id": conv} if conv else {})}

    def guardrail_reply(state: AgentState) -> dict:
        return {"answer": R.GUARDRAIL_MESSAGES[state["decision"]["guardrail"]]}

    # ---- wiring
    node_for = {R.GENIE: "genie", R.UC: "uc_functions", R.DOCS: "documents"}

    def dispatch(state: AgentState):
        if state["decision"].get("guardrail"):
            return "guardrail_reply"
        return [node_for[r] for r in state.get("pending", [])] or "synthesize"

    def after_check(state: AgentState):
        return [node_for[r] for r in state.get("pending", [])] or "synthesize"

    g = StateGraph(AgentState)
    g.add_node("guard_and_route", guard_and_route)
    g.add_node("genie", genie_node)
    g.add_node("uc_functions", uc_node)
    g.add_node("documents", docs_node)
    g.add_node("check", check)
    g.add_node("synthesize", synthesize)
    g.add_node("guardrail_reply", guardrail_reply)
    g.add_edge(START, "guard_and_route")
    targets = ["genie", "uc_functions", "documents"]
    g.add_conditional_edges("guard_and_route", dispatch, targets + ["guardrail_reply", "synthesize"])
    for t in targets:
        g.add_edge(t, "check")
    g.add_conditional_edges("check", after_check, targets + ["synthesize"])
    g.add_edge("synthesize", END)
    g.add_edge("guardrail_reply", END)
    return g.compile()


# --------------------------------------------------------------------------- ResponsesAgent wrapper

def _messages(request: ResponsesAgentRequest) -> list[dict]:
    out = []
    for item in request.input:
        d = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        if d.get("role") not in ("user", "assistant"):
            continue
        content = d.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        out.append({"role": d["role"], "content": content or ""})
    return out


class HealthcareRouterAgent(ResponsesAgent):
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.graph = build_graph(cfg)

    def _run(self, request: ResponsesAgentRequest) -> tuple[str, dict]:
        msgs = _messages(request)
        if not msgs or msgs[-1]["role"] != "user":
            return "Please ask a question.", {}
        custom = request.custom_inputs or {}
        state: AgentState = {
            "question": msgs[-1]["content"],
            "history": msgs[:-1],
            "results": {},
            "routes_tried": [],
            "genie_conversation_id": custom.get("genie_conversation_id"),
        }
        if custom.get("force_route"):
            forced = custom["force_route"]
            state["decision"] = {"forced": [forced] if isinstance(forced, str) else list(forced)}
        final = self.graph.invoke(state)
        d = final.get("decision", {})
        custom_out = {
            "routes": d.get("routes", []),
            "route_source": d.get("source"),
            "route_reason": d.get("reason"),
            "route_confidence": d.get("confidence"),
            "guardrail": d.get("guardrail"),
            "routes_tried": list(dict.fromkeys(final.get("routes_tried", []))),
            "errors": {k: v["error"] for k, v in final.get("results", {}).items() if v.get("error")},
            "standalone_question": final.get("standalone_question"),
            "genie_conversation_id": final.get("genie_conversation_id"),
        }
        return final.get("answer", ""), json.loads(json.dumps(custom_out, default=str))

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        text, custom = self._run(request)
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=text, id=str(uuid4()))], custom_outputs=custom)

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        text, custom = self._run(request)
        yield ResponsesAgentStreamEvent(type="response.output_item.done",
                                        item=self.create_text_output_item(text=text, id=str(uuid4())),
                                        custom_outputs=custom)


def _load_config() -> dict:
    try:
        dev = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    except NameError:
        dev = "config.yaml"
    return ModelConfig(development_config=dev).to_dict()


AGENT = HealthcareRouterAgent(_load_config())
mlflow.models.set_model(AGENT)
