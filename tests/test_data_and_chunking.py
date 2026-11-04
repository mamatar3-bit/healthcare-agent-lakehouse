import csv
from datetime import date
from pathlib import Path

import hc_synth as H
from chunking import chunk_folder, chunk_markdown

ROOT = Path(__file__).resolve().parents[1]


def _seed(name):
    with open(ROOT / "dbt" / "seeds" / name) as f:
        return list(csv.DictReader(f))


def test_generator_ids_unique_and_codes_match_seeds():
    data = H.generate(H.SynthConfig(n_patients=300))
    enc_ids = [e["encounter_id"] for e in data["encounters"]]
    assert len(enc_ids) == len(set(enc_ids))
    dx = {r["dx_code"] for r in _seed("icd10_codes.csv")}
    cpt = {r["cpt_code"] for r in _seed("cpt_codes.csv")}
    payers = {r["payer_id"] for r in _seed("payers.csv")}
    assert {e["primary_dx_code"] for e in data["encounters"]} <= dx
    assert {c["cpt_code"] for c in data["claims"]} <= cpt
    assert {p["payer_id"] for p in data["patients"]} <= payers
    assert all(c["paid_amount"] <= c["billed_amount"] for c in data["claims"])
    assert all(e["discharge_ts"] >= e["admit_ts"] for e in data["encounters"])


def test_generator_has_readmissions_and_denials():
    data = H.generate(H.SynthConfig(n_patients=800))
    statuses = {c["claim_status"] for c in data["claims"]}
    assert {"paid", "denied", "pending"} <= statuses
    assert sum(e["encounter_type"] == "inpatient" for e in data["encounters"]) > 20


def test_incremental_ids_do_not_collide():
    full = H.generate(H.SynthConfig(n_patients=200))
    inc = H.generate(H.SynthConfig(n_patients=200), incremental_day=date(2026, 11, 4))
    assert not {c["claim_id"] for c in full["claims"]} & {c["claim_id"] for c in inc["claims"]}
    assert "patients" not in inc


def test_chunking_keeps_title_and_section():
    md = "# Policy X\n\nIntro text.\n\n## Deadlines\nAppeal within 60 days.\n\n## Empty\n"
    rows = chunk_markdown("policy_x", md)
    assert [r["section"] for r in rows] == ["Overview", "Deadlines"]
    assert rows[1]["content"].startswith("Policy X — Deadlines")
    assert len({r["chunk_id"] for r in rows}) == 2


def test_policy_docs_chunk():
    rows = chunk_folder(ROOT / "docs" / "policies")
    assert len(rows) > 30
    assert max(len(r["content"]) for r in rows) < 1600
