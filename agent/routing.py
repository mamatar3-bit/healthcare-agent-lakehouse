"""Deterministic routing layer for the healthcare agent.

Pure Python with no Databricks dependencies, so it is unit-tested in CI (tests/test_routing.py).
The agent uses it in three ways:
  1. Guardrails run first (clinical advice for an individual, emergencies, bulk PHI requests).
  2. Rule scores decide the route when they are confident.
  3. Otherwise the scores and extracted entities are handed to the LLM router as hints.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GENIE, UC, DOCS = "genie", "uc_functions", "documents"
ROUTES = (GENIE, UC, DOCS)

# If every route tried fails or comes back empty, try these next (in order).
FALLBACKS = {GENIE: [DOCS], UC: [GENIE, DOCS], DOCS: [GENIE]}

DRUGS = {
    "metformin", "insulin glargine", "semaglutide", "lisinopril", "amlodipine", "furosemide",
    "spironolactone", "atorvastatin", "simvastatin", "apixaban", "warfarin", "amiodarone",
    "sertraline", "ibuprofen", "tramadol", "prednisone", "clarithromycin", "adalimumab",
}
PAYER_ALIASES = {
    r"\bsummit (health )?ppo\b": "PAY-01",
    r"\bsummit (health )?hmo\b": "PAY-02",
    r"\b(bluepoint|medicare advantage)\b": "PAY-03",
    r"\bcommonwealth medicaid\b": "PAY-04",
    r"\bself[- ]pay\b": "PAY-05",
}

RE_PATIENT = re.compile(r"\bPAT-?\d{3,6}\b", re.I)
RE_CLAIM = re.compile(r"\bCLM-?\d{4,}\b", re.I)
RE_PAYER = re.compile(r"\bPAY-?0?[1-5]\b", re.I)
RE_CPT = re.compile(r"(?<![\d-])\d{5}(?![\d-])")

# (pattern, weight) per route
GENIE_PATTERNS = [
    (r"\bhow many\b", 1.5), (r"\b(count|number) of\b", 1.5), (r"\b(total|sum)\b", 1.0),
    (r"\b(average|avg|mean|median)\b", 1.5), (r"\brates?\b", 1.0), (r"\b(percent|percentage|share|proportion)\b", 1.0),
    (r"\btrends?\b|\bover time\b|\bmonth over month\b|\byear over year\b", 2.0),
    (r"\b(by|per|each|across) (month|week|quarter|year|facility|facilities|payer|payers|payer type|specialty|provider|age band|diagnosis|category)\b", 2.0),
    (r"\btop \d+\b|\b(highest|lowest|most|fewest|rank|ranking)\b", 1.5),
    (r"\bcompare\b|\bvs\.?\b|\bversus\b", 1.0),
    (r"\b(last|this|previous|past) (month|quarter|year|week|\d+ months)\b|\bytd\b|\bin 20\d\d\b", 1.0),
    (r"\b(volume|utilization|admissions|encounters|visits|denials|readmissions)\b", 0.5),
    (r"\b(chart|plot|graph|dashboard)\b", 1.0),
]
DOC_PATTERNS = [
    (r"\bpolic(y|ies)\b|\bprotocol\b|\bguideline\b|\bstandard\b|\bprogram guide\b", 2.5),
    (r"\bhow (do|should|can) (i|we|staff)\b|\bhow to\b", 2.0),
    (r"\bwhat (is|are) the (process|steps|requirements?|criteria|rules?|deadline)\b", 2.5),
    (r"\b(process|procedure|steps|workflow)\b", 1.0),
    (r"\b(eligib(le|ility)|criteria|required documentation|documentation)\b", 1.5),
    (r"\b(allowed|permitted|must|required to)\b", 1.0),
    (r"\bhow (is|are) .{1,40} (measured|defined|calculated|scored)\b|\bwhat does .{1,40} mean\b|\bdefin(e|ition)\b", 2.5),
    (r"\b(appeal window|appeal deadline|turnaround|timely filing|step therapy|teach-back|hipaa|minimum necessary|telehealth|care management|lace)\b", 1.0),
    (r"\bwhat should (i|we) do\b|\bnext steps?\b|\bhow (do|should) we (handle|fix|resolve)\b", 2.5),
    (r"\bhow long (do|does|is|can|should)\b|\bwithin how many\b", 2.0),
    (r"\bwho (qualifies|is eligible|can|should)\b|\bqualif(y|ies|ied)\b", 2.0),
    (r"\bcan (i|we|staff|patients|providers|clinicians|nurses)\b", 1.5),
    (r"\b(appeal|retroactive|exception request|referral)\b", 0.5),
]
UC_KEYWORDS = [
    (r"\b(status|look ?up|details?|summary|snapshot)\b", 0.5),
    (r"\b(interact(ion|s)?|together with|combine|combined)\b", 0.5),
    (r"\b(formulary|tier|covered|coverage)\b", 0.5),
    (r"\b(prior auth(orization)?|\bpa\b)\b", 0.5),
    (r"\breadmission risk\b|\blace score\b", 0.5),
]

EMERGENCY = re.compile(
    r"\b(chest pain right now|can'?t breathe|cannot breathe|overdos(e|ed)|suicid(e|al)|kill myself|want to die|stroke symptoms)\b", re.I)
CLINICAL_ADVICE = re.compile(
    r"\b(should i (take|stop|start|increase|decrease|double|skip)|what dose should i|is it safe for me to|"
    r"diagnose me|do i have|my (symptoms|pain|rash|fever|blood sugar)|what('?s| is) wrong with me|prescribe me)\b", re.I)
BULK_PHI = re.compile(
    r"\b(list|export|dump|download|give me|show( me)?)\b.{0,40}\b(all|every|each)?\s*patients?\b.{0,60}"
    r"\b(names?|addresses|address|dob|dates? of birth|birth ?dates?|ssn|social security|phone( numbers?)?|member ids?)\b", re.I)


@dataclass
class Entities:
    patient_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    payer_ids: list[str] = field(default_factory=list)
    cpt_codes: list[str] = field(default_factory=list)
    drugs: list[str] = field(default_factory=list)

    def as_hint(self) -> str:
        parts = [f"{k}={v}" for k, v in self.__dict__.items() if v]
        return ", ".join(parts) or "none"


@dataclass
class RouteDecision:
    routes: list[str]
    reason: str
    source: str  # "rules", "llm", "guardrail", "default"
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    guardrail: str | None = None  # "emergency", "clinical_advice", "bulk_phi"
    confident: bool = False  # True when rules alone are trusted (no LLM call needed)


def _norm_id(raw: str, prefix: str, width: int) -> str:
    digits = re.sub(r"\D", "", raw)
    return f"{prefix}-{int(digits):0{width}d}"


def extract_entities(text: str) -> Entities:
    lower = text.lower()
    payers = [_norm_id(m, "PAY", 2) for m in RE_PAYER.findall(text)]
    for pattern, pid in PAYER_ALIASES.items():
        if re.search(pattern, lower):
            payers.append(pid)
    claim_spans = [m.span() for m in RE_CLAIM.finditer(text)] + [m.span() for m in RE_PATIENT.finditer(text)]
    cpts = [m.group(0) for m in RE_CPT.finditer(text)
            if not any(s <= m.start() < e for s, e in claim_spans) and not re.match(r"20\d\d\d", m.group(0))]
    return Entities(
        patient_ids=sorted({_norm_id(m, "PAT", 6) for m in RE_PATIENT.findall(text)}),
        claim_ids=sorted({_norm_id(m, "CLM", 7) for m in RE_CLAIM.findall(text)}),
        payer_ids=sorted(set(payers)),
        cpt_codes=sorted(set(cpts)),
        drugs=sorted(d for d in DRUGS if re.search(rf"\b{re.escape(d)}\b", lower)),
    )


def check_guardrails(text: str) -> str | None:
    if EMERGENCY.search(text):
        return "emergency"
    if BULK_PHI.search(text):
        return "bulk_phi"
    if CLINICAL_ADVICE.search(text):
        return "clinical_advice"
    return None


def _score(patterns, text: str) -> float:
    return sum(w for p, w in patterns if re.search(p, text, re.I))


def score_routes(text: str, ents: Entities) -> tuple[dict[str, float], list[str]]:
    signals: list[str] = []
    uc = _score(UC_KEYWORDS, text)
    if ents.patient_ids:
        uc += 3.5; signals.append(f"patient id {ents.patient_ids}")
    if ents.claim_ids:
        uc += 3.5; signals.append(f"claim id {ents.claim_ids}")
    if ents.cpt_codes and ents.payer_ids:
        uc += 3.0; signals.append("cpt code + payer")
    if len(ents.drugs) >= 2 and re.search(r"interact|together|combine|with", text, re.I):
        uc += 3.0; signals.append("drug pair")
    if ents.drugs and ents.payer_ids and re.search(r"formulary|tier|cover", text, re.I):
        uc += 3.0; signals.append("drug + payer coverage")

    genie = _score(GENIE_PATTERNS, text)
    docs = _score(DOC_PATTERNS, text)
    # A single identified entity is rarely an aggregate question.
    if ents.patient_ids or ents.claim_ids:
        genie *= 0.4
    if genie:
        signals.append("aggregate/metric language")
    if docs:
        signals.append("policy/process language")
    return {GENIE: round(genie, 2), UC: round(uc, 2), DOCS: round(docs, 2)}, signals


def rule_route(text: str, available: tuple[str, ...] = ROUTES, threshold: float = 0.7) -> RouteDecision:
    """Score-based decision. confidence < threshold means: ask the LLM router."""
    guard = check_guardrails(text)
    if guard:
        return RouteDecision([], f"guardrail: {guard}", "guardrail", 1.0, guardrail=guard, confident=True)

    ents = extract_entities(text)
    scores, signals = score_routes(text, ents)
    ranked = sorted(((s, r) for r, s in scores.items() if r in available), reverse=True)
    if not ranked or ranked[0][0] < 1.0:
        return RouteDecision([DOCS] if DOCS in available else list(available[:1]),
                             "no strong signal; defaulting to documents", "default", 0.2, scores)

    top_score, top = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    routes = [top]
    # Multi-route: an ID lookup plus a "what do we do about it" question, etc.
    for s, r in ranked[1:]:
        if s >= 2.0 and s >= 0.5 * top_score:
            routes.append(r)
    competitor = 0.0 if len(routes) > 1 else second_score
    confidence = round(top_score / (top_score + competitor + 1.0), 2)
    if len(routes) > 1:
        confidence = round(min(top_score, ranked[1][0]) / (min(top_score, ranked[1][0]) + 1.0), 2)
    reason = f"rules: {'; '.join(signals) or 'keywords'} (scores {scores})"
    return RouteDecision(routes, reason, "rules", confidence, scores, confident=confidence >= threshold)


def fallback_routes(tried: list[str], available: tuple[str, ...]) -> list[str]:
    for r in tried:
        for nxt in FALLBACKS.get(r, []):
            if nxt in available and nxt not in tried:
                return [nxt]
    return []


GUARDRAIL_MESSAGES = {
    "emergency": (
        "This sounds like it may be an emergency. If someone is in immediate danger, call 911 now. "
        "For a mental health crisis in the US, call or text 988 (Suicide & Crisis Lifeline). "
        "I can't help with emergencies, but I'm here for policy, claims and analytics questions."
    ),
    "clinical_advice": (
        "I can't give individual diagnosis, dosing or treatment advice; those decisions belong with the "
        "treating clinician. I can look up network policies (for example discharge, formulary or "
        "medication-reconciliation protocols), check a drug pair in the reference table, or summarize a "
        "patient record by ID for a care team member."
    ),
    "bulk_phi": (
        "I can't export identified patient details such as names, birth dates or contact information. "
        "Under the HIPAA minimum necessary standard, bulk extracts of identified PHI need Privacy Office "
        "approval. I can give you de-identified counts and rates instead, or look up one patient by ID."
    ),
}
