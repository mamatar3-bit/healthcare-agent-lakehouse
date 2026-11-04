import pytest
import routing as R

CASES = [
    # (question, expected primary route, other routes allowed)
    ("What is the 30-day readmission rate by facility for the last 6 months?", R.GENIE),
    ("Which payer has the highest claim denial rate this year?", R.GENIE),
    ("Top 5 diagnosis categories by inpatient admissions last quarter", R.GENIE),
    ("How many prior auth denials per month in 2026?", R.GENIE),
    ("What is the status of claim CLM-0004521?", R.UC),
    ("Give me a summary of patient PAT-000123", R.UC),
    ("Does Summit PPO require prior auth for 72148?", R.UC),
    ("Is there an interaction between warfarin and ibuprofen?", R.UC),
    ("Is semaglutide covered on PAY-03 formulary?", R.UC),
    ("What is the readmission risk for PAT-000045?", R.UC),
    ("What is the process for retroactive authorization?", R.DOCS),
    ("How long do we have to appeal a Medicare Advantage denial?", R.DOCS),
    ("Who qualifies for care management?", R.DOCS),
    ("How is the readmission rate measured?", R.DOCS),
]


@pytest.mark.parametrize("question,expected", CASES)
def test_primary_route(question, expected):
    d = R.rule_route(question)
    assert d.routes[0] == expected, (d.routes, d.reason)


def test_multi_route_lookup_plus_policy():
    d = R.rule_route("Claim CLM-0000017 was denied for missing prior auth, what should we do next?")
    assert d.routes[0] == R.UC and R.DOCS in d.routes


def test_id_lookups_are_confident():
    assert R.rule_route("What is the status of claim CLM-0004521?").confident


def test_vague_question_defers_to_llm():
    d = R.rule_route("what about telehealth")
    assert not d.confident


@pytest.mark.parametrize("question,guard", [
    ("Should I stop taking my warfarin?", "clinical_advice"),
    ("Export all patients with names and dates of birth", "bulk_phi"),
    ("I want to kill myself", "emergency"),
])
def test_guardrails(question, guard):
    d = R.rule_route(question)
    assert d.guardrail == guard and d.routes == []
    assert R.GUARDRAIL_MESSAGES[guard]


def test_genie_unavailable_routes_elsewhere():
    d = R.rule_route("Readmission rate by facility last year", available=(R.UC, R.DOCS))
    assert R.GENIE not in d.routes


def test_entity_extraction_normalizes_ids():
    e = R.extract_entities("pat-000045 had claim clm-0000017 with PAY-1 for CPT 72148 in 20261; warfarin and ibuprofen")
    assert e.patient_ids == ["PAT-000045"]
    assert e.claim_ids == ["CLM-0000017"]
    assert e.payer_ids == ["PAY-01"]
    assert e.cpt_codes == ["72148"]
    assert e.drugs == ["ibuprofen", "warfarin"]


def test_payer_alias_and_no_year_as_cpt():
    e = R.extract_entities("Does Bluepoint need PA for 27447 in 2026?")
    assert e.payer_ids == ["PAY-03"] and e.cpt_codes == ["27447"]


def test_fallbacks():
    assert R.fallback_routes([R.UC], R.ROUTES) == [R.GENIE]
    assert R.fallback_routes([R.UC, R.GENIE], R.ROUTES) == [R.DOCS]
    assert R.fallback_routes([R.UC, R.GENIE, R.DOCS], R.ROUTES) == []
