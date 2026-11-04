"""Runs the real agent graph (routing, fan-out, fallback, synthesis, ResponsesAgent wrapper)
with fake Databricks services, so CI can test the wiring without a workspace."""
import sys
import types
from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

CALLS: list[str] = []
STATE = {"genie_fails": False, "docs_empty": False, "router_routes": ["documents"]}


class FakeLLM:
    def __init__(self, endpoint=None, temperature=0, **_):
        pass

    def invoke(self, messages):
        text = messages if isinstance(messages, str) else " ".join(getattr(m, "content", "") for m in messages)
        if "standalone question" in text:
            CALLS.append("condense")
            return AIMessage("What is the denial rate for Medicaid by month?")
        if "policy excerpts" in text:
            CALLS.append("docs_llm")
            return AIMessage("NOT_IN_DOCS" if STATE["docs_empty"] else "Appeal within 65 days [Claims Denial — Appeal deadlines].")
        if "Combine these partial answers" in text:
            CALLS.append("synth")
            return AIMessage("COMBINED ANSWER")
        return AIMessage("ok")

    def bind_tools(self, tools):
        return FakeBound()

    def with_structured_output(self, schema):
        class Router:
            def invoke(self, messages):
                CALLS.append("llm_router")
                return schema(routes=STATE["router_routes"], reason="fake llm")
        return Router()


class FakeBound:
    def __init__(self):
        self.n = 0

    def invoke(self, messages):
        self.n += 1
        if self.n == 1:
            return AIMessage("", tool_calls=[{"name": "hc_dev__ai__get_claim_status",
                                              "args": {"target_claim_id": "CLM-0000017"}, "id": "t1"}])
        return AIMessage("Claim CLM-0000017 is DENIED (CO-197).")


class FakeTool:
    def __init__(self, name):
        self.name = name

    def invoke(self, args):
        CALLS.append(f"tool:{self.name}")
        return "Claim CLM-0000017 ... Status: DENIED"


class FakeToolkit:
    def __init__(self, function_names, client=None):
        self.tools = [FakeTool(n.replace(".", "__")) for n in function_names]


class FakeRetriever:
    def __init__(self, **_):
        pass

    def invoke(self, q):
        CALLS.append("retrieve")
        return [Document(page_content="Medicare Advantage plans allow 65 days.",
                         metadata={"doc_title": "Claims Denial", "section": "Appeal deadlines"})]


class FakeGenie:
    def __init__(self, **_):
        pass

    def invoke(self, payload):
        CALLS.append("genie")
        if STATE["genie_fails"]:
            raise TimeoutError("genie timeout")
        return {"messages": [{"role": "assistant", "content": "Medicaid denial rate: 14.2%"}], "conversation_id": "conv-1"}


def _install_fakes():
    dl = types.ModuleType("databricks_langchain")
    dl.ChatDatabricks, dl.VectorSearchRetrieverTool = FakeLLM, FakeRetriever
    genie = types.ModuleType("databricks_langchain.genie")
    genie.GenieAgent = FakeGenie
    uc_tk = types.ModuleType("unitycatalog.ai.langchain.toolkit")
    uc_tk.UCFunctionToolkit = FakeToolkit
    uc_db = types.ModuleType("unitycatalog.ai.core.databricks")
    uc_db.DatabricksFunctionClient = lambda: None
    for name in ("unitycatalog", "unitycatalog.ai", "unitycatalog.ai.langchain", "unitycatalog.ai.core"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules.update({"databricks_langchain": dl, "databricks_langchain.genie": genie,
                        "unitycatalog.ai.langchain.toolkit": uc_tk, "unitycatalog.ai.core.databricks": uc_db})


@pytest.fixture(scope="module")
def agent_mod():
    _install_fakes()
    import mlflow
    mlflow.langchain.autolog = lambda *a, **k: None  # no tracking server in CI
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    import agent as A
    return A


def make_agent(A, **overrides):
    cfg = A._load_config()
    cfg.update({"genie_space_id": "space-1", **overrides})
    return A.HealthcareRouterAgent(cfg)


def ask(agent, text, history=None, custom=None):
    from mlflow.types.responses import ResponsesAgentRequest
    msgs = (history or []) + [{"role": "user", "content": text}]
    resp = agent.predict(ResponsesAgentRequest(input=msgs, custom_inputs=custom or {}))
    answer = resp.output[0].content[0]["text"]
    return answer, resp.custom_outputs


@pytest.fixture(autouse=True)
def reset():
    CALLS.clear()
    STATE.update(genie_fails=False, docs_empty=False, router_routes=["documents"])


def test_uc_lookup_route(agent_mod):
    answer, out = ask(make_agent(agent_mod), "What is the status of claim CLM-0000017?")
    assert out["routes"] == ["uc_functions"] and out["route_source"] == "rules"
    assert "DENIED" in answer and "Sources: UC function get_claim_status" in answer
    assert "llm_router" not in CALLS


def test_genie_route_and_conversation_id(agent_mod):
    answer, out = ask(make_agent(agent_mod), "What is the denial rate by payer this year?")
    assert out["routes"] == ["genie"] and "14.2%" in answer
    assert out["genie_conversation_id"] == "conv-1"


def test_multi_route_fans_out_and_synthesizes(agent_mod):
    answer, out = ask(make_agent(agent_mod),
                      "Claim CLM-0000017 was denied for missing prior auth, what should we do next?")
    assert set(out["routes"]) == {"uc_functions", "documents"}
    assert answer == "COMBINED ANSWER" and "synth" in CALLS


def test_genie_failure_falls_back_to_documents(agent_mod):
    STATE["genie_fails"] = True
    answer, out = ask(make_agent(agent_mod), "What is the denial rate by payer this year?")
    assert out["routes_tried"] == ["genie", "documents"]
    assert "65 days" in answer and "genie" in out["errors"]


def test_low_confidence_uses_llm_router(agent_mod):
    answer, out = ask(make_agent(agent_mod), "what about telehealth")
    assert out["route_source"] == "llm" and "llm_router" in CALLS


def test_genie_disabled_without_space(agent_mod):
    answer, out = ask(make_agent(agent_mod, genie_space_id=""), "What is the denial rate by payer this year?")
    assert "genie" not in out["routes_tried"]


def test_guardrail_short_circuits(agent_mod):
    answer, out = ask(make_agent(agent_mod), "Should I stop taking my warfarin?")
    assert out["guardrail"] == "clinical_advice" and "clinician" in answer
    assert CALLS == []


def test_follow_up_is_condensed(agent_mod):
    history = [{"role": "user", "content": "What is the denial rate for Medicaid?"},
               {"role": "assistant", "content": "14.2%"}]
    _, out = ask(make_agent(agent_mod), "and by month?", history=history)
    assert "condense" in CALLS and out["standalone_question"].startswith("What is the denial rate")


def test_nothing_found_message(agent_mod):
    STATE.update(docs_empty=True, genie_fails=True)
    answer, out = ask(make_agent(agent_mod), "What is the process for retroactive authorization?")
    assert "couldn't find" in answer


def test_force_route(agent_mod):
    _, out = ask(make_agent(agent_mod), "What is the status of claim CLM-0000017?",
                 custom={"force_route": "documents"})
    assert out["routes"] == ["documents"] and out["route_source"] == "forced"
