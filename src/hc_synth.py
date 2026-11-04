"""Synthetic healthcare data generator (no real PHI).

Pure Python so it can be unit-tested locally; the 01_generate_bronze notebook
turns the output into Delta tables. Reference codes here must match the dbt
seeds in dbt/seeds (dbt relationship tests catch any drift).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

PAYERS = ["PAY-01", "PAY-02", "PAY-03", "PAY-04", "PAY-05"]
PAYER_WEIGHTS = [0.30, 0.20, 0.25, 0.20, 0.05]

# code -> (is_chronic, encounter types it typically drives)
ICD10 = {
    "E11.9": True, "I10": True, "I50.9": True, "J44.1": True, "N18.30": True,
    "F32.9": True, "E78.5": True, "I48.91": True,
    "J18.9": False, "I21.4": False, "A41.9": False, "S72.001A": False,
    "J06.9": False, "M54.50": False, "N39.0": False, "Z00.00": False,
}
CHRONIC = [c for c, chronic in ICD10.items() if chronic]
INPATIENT_DX = ["J18.9", "I21.4", "A41.9", "S72.001A", "I50.9", "J44.1"]
ED_DX = ["J18.9", "N39.0", "M54.50", "I21.4", "J06.9", "S72.001A"]
OUTPATIENT_DX = ["E11.9", "I10", "E78.5", "F32.9", "M54.50", "J06.9", "Z00.00", "N18.30", "I48.91"]

CPT_BY_TYPE = {
    "outpatient": ["99213", "99214", "80053", "83036", "93000", "70553", "72148", "93306", "45378", "97110"],
    "telehealth": ["99213", "99214"],
    "emergency": ["99285", "71046", "80053", "93000"],
    "inpatient": ["99223", "80053", "71046", "93306", "27447"],
}
CPT_PRICE = {
    "99213": 135, "99214": 195, "99285": 980, "99223": 410, "70553": 2600, "72148": 1650,
    "93306": 1250, "45378": 2100, "27447": 32000, "80053": 48, "83036": 38, "71046": 120,
    "97110": 95, "93000": 60,
}
DENIAL_REASONS = [
    "CO-197 Prior authorization absent", "CO-16 Missing information", "CO-50 Not medically necessary",
    "CO-29 Timely filing limit expired", "CO-22 Coordination of benefits",
]
DRUGS_BY_DX = {
    "E11.9": ["metformin", "insulin glargine", "semaglutide"],
    "I10": ["lisinopril", "amlodipine"],
    "I50.9": ["furosemide", "spironolactone", "lisinopril"],
    "E78.5": ["atorvastatin", "simvastatin"],
    "I48.91": ["apixaban", "warfarin", "amiodarone"],
    "F32.9": ["sertraline"],
    "M54.50": ["ibuprofen", "tramadol"],
    "J44.1": ["prednisone"],
    "J18.9": ["clarithromycin"],
}
FACILITIES = ["Riverbend General", "Riverbend North", "Lakeside Community", "Riverbend Telehealth"]
SPECIALTIES = ["Family Medicine", "Internal Medicine", "Cardiology", "Endocrinology",
               "Emergency Medicine", "Hospitalist", "Orthopedics", "Pulmonology", "Psychiatry"]
FIRST = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Quinn", "Drew",
         "Sam", "Robin", "Kai", "Rowan", "Emery", "Parker", "Reese", "Skyler", "Hayden", "Logan"]
LAST = ["Rivera", "Chen", "Patel", "Okafor", "Novak", "Haddad", "Kim", "Santos", "Larsen", "Mendez",
        "Brooks", "Ibrahim", "Moreau", "Tanaka", "Walsh", "Fischer", "Abara", "Lindqvist", "Ortiz", "Grant"]
DISPOSITIONS = ["home", "home_health", "snf", "rehab", "ama", "expired"]
DISPOSITION_W = [0.62, 0.15, 0.12, 0.07, 0.03, 0.01]


@dataclass
class SynthConfig:
    n_patients: int = 2000
    n_providers: int = 120
    months: int = 24
    end_date: date = field(default_factory=date.today)
    seed: int = 42


def _rng_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))


def make_providers(cfg: SynthConfig, rng: random.Random) -> list[dict]:
    rows = []
    for i in range(1, cfg.n_providers + 1):
        rows.append({
            "provider_id": f"PRV-{i:04d}",
            "npi": str(1_000_000_000 + i * 7919),  # synthetic, not real NPIs
            "provider_name": f"Dr. {rng.choice(FIRST)} {rng.choice(LAST)}",
            "specialty": SPECIALTIES[i % len(SPECIALTIES)],
            "facility": rng.choice(FACILITIES[:3]),
            "state": "VA",
        })
    return rows


def make_patients(cfg: SynthConfig, rng: random.Random, providers: list[dict]) -> list[dict]:
    pcps = [p["provider_id"] for p in providers if p["specialty"] in ("Family Medicine", "Internal Medicine")]
    rows = []
    for i in range(1, cfg.n_patients + 1):
        age = int(min(max(rng.gauss(52, 19), 1), 98))
        payer = rng.choices(PAYERS, PAYER_WEIGHTS)[0]
        if age >= 65 and rng.random() < 0.8:
            payer = "PAY-03"
        birth = cfg.end_date - timedelta(days=age * 365 + rng.randint(0, 364))
        n_chronic = rng.choices([0, 1, 2, 3, 4], [0.35, 0.25, 0.2, 0.12, 0.08])[0]
        if age > 60:
            n_chronic = min(n_chronic + 1, 5)
        rows.append({
            "patient_id": f"PAT-{i:06d}",
            "first_name": rng.choice(FIRST),
            "last_name": rng.choice(LAST),
            "birth_date": birth,
            "sex": rng.choice(["F", "M"]),
            "zip": f"23{rng.randint(100, 299)}",
            "payer_id": payer,
            "member_id": f"M{rng.randint(10**8, 10**9 - 1)}",
            "pcp_provider_id": rng.choice(pcps),
            "chronic_conditions": ",".join(rng.sample(CHRONIC, n_chronic)),  # dropped before writing bronze
            "created_at": datetime.combine(birth, datetime.min.time()) + timedelta(days=365 * 18),
        })
    return rows


def _encounter(rng, cfg, patient, providers_by_spec, enc_id, etype, day, dx):
    spec = {"inpatient": "Hospitalist", "emergency": "Emergency Medicine"}.get(etype)
    provider = rng.choice(providers_by_spec[spec]) if spec else patient["pcp_provider_id"]
    admit = datetime.combine(day, datetime.min.time()) + timedelta(hours=rng.randint(6, 22))
    if etype == "inpatient":
        los = max(1, int(rng.expovariate(1 / 4.2)))
        discharge = admit + timedelta(days=los, hours=rng.randint(-3, 5))
        disposition = rng.choices(DISPOSITIONS, DISPOSITION_W)[0]
    elif etype == "emergency":
        discharge = admit + timedelta(hours=rng.randint(2, 11))
        disposition = "home"
    else:
        discharge = admit + timedelta(minutes=rng.choice([20, 30, 40, 60]))
        disposition = "home"
    facility = "Riverbend Telehealth" if etype == "telehealth" else rng.choice(FACILITIES[:3])
    return {
        "encounter_id": enc_id, "patient_id": patient["patient_id"], "provider_id": provider,
        "encounter_type": etype, "admit_ts": admit, "discharge_ts": discharge, "facility": facility,
        "primary_dx_code": dx, "discharge_disposition": disposition,
    }


def generate(cfg: SynthConfig | None = None, incremental_day: date | None = None) -> dict[str, list[dict]]:
    """Full history, or (incremental_day set) one day of new activity with date-stamped IDs."""
    cfg = cfg or SynthConfig()
    rng = random.Random(cfg.seed if incremental_day is None else cfg.seed + incremental_day.toordinal())
    providers = make_providers(cfg, random.Random(cfg.seed))
    patients = make_patients(cfg, random.Random(cfg.seed + 1), providers)
    by_spec: dict[str, list[str]] = {}
    for p in providers:
        by_spec.setdefault(p["specialty"], []).append(p["provider_id"])

    start = cfg.end_date - timedelta(days=30 * cfg.months)
    prefix = "" if incremental_day is None else incremental_day.strftime("%Y%m%d")
    seq = {"enc": 0, "clm": 0, "rx": 0, "lab": 0}

    def nid(kind, tag):
        seq[kind] += 1
        return f"{tag}-{prefix}{seq[kind]:07d}"

    encounters, diagnoses, claims, rx, labs = [], [], [], [], []

    patient_iter = patients if incremental_day is None else rng.sample(patients, max(1, len(patients) // 60))
    for pt in patient_iter:
        chronic = [c for c in pt["chronic_conditions"].split(",") if c]
        if incremental_day is None:
            n_out = rng.randint(1, 3) * (1 + len(chronic))
            plan = [("outpatient" if rng.random() < 0.8 else "telehealth", _rng_date(rng, start, cfg.end_date))
                    for _ in range(n_out)]
            frailty = 0.04 + 0.06 * len(chronic)
            plan += [("emergency", _rng_date(rng, start, cfg.end_date)) for _ in range(int(rng.random() < frailty * 2) + int(rng.random() < frailty))]
            for _ in range(int(rng.random() < frailty) + int(rng.random() < frailty / 2)):
                d = _rng_date(rng, start, cfg.end_date - timedelta(days=10))
                plan.append(("inpatient", d))
                if rng.random() < 0.10 + 0.05 * len(chronic):  # inject 30-day readmissions
                    plan.append(("inpatient", d + timedelta(days=rng.randint(6, 29))))
        else:
            plan = [(rng.choices(["outpatient", "telehealth", "emergency", "inpatient"], [0.6, 0.2, 0.15, 0.05])[0], incremental_day)]

        for etype, day in plan:
            if day > cfg.end_date and incremental_day is None:
                continue
            pool = {"inpatient": INPATIENT_DX, "emergency": ED_DX}.get(etype, OUTPATIENT_DX)
            dx = rng.choice(chronic) if chronic and etype in ("outpatient", "telehealth") and rng.random() < 0.7 else rng.choice(pool)
            enc = _encounter(rng, cfg, pt, by_spec, nid("enc", "ENC"), etype, day, dx)
            encounters.append(enc)
            for rank, code in enumerate([dx] + [c for c in chronic if c != dx][:3], start=1):
                diagnoses.append({"encounter_id": enc["encounter_id"], "dx_code": code, "dx_rank": rank})

            cpts = rng.sample(CPT_BY_TYPE[etype], k=min(len(CPT_BY_TYPE[etype]), rng.randint(1, 3)))
            submitted = enc["discharge_ts"].date() + timedelta(days=rng.randint(1, 7))
            for cpt in cpts:
                billed = round(CPT_PRICE[cpt] * rng.uniform(0.9, 1.25), 2)
                age_days = (cfg.end_date - submitted).days
                status = "pending" if age_days < 14 else rng.choices(["paid", "denied", "appealed"], [0.84, 0.12, 0.04])[0]
                needs_pa = cpt in ("70553", "72148", "27447") and pt["payer_id"] in ("PAY-01", "PAY-02", "PAY-03")
                if needs_pa and status == "paid" and rng.random() < 0.25:
                    status = "denied"
                allowed = round(billed * {"PAY-03": 0.55, "PAY-04": 0.45, "PAY-05": 1.0}.get(pt["payer_id"], 0.7), 2)
                claims.append({
                    "claim_id": nid("clm", "CLM"), "encounter_id": enc["encounter_id"], "patient_id": pt["patient_id"],
                    "payer_id": pt["payer_id"], "cpt_code": cpt, "billed_amount": billed,
                    "allowed_amount": allowed if status != "denied" else 0.0,
                    "paid_amount": round(allowed * 0.9, 2) if status == "paid" else 0.0,
                    "claim_status": status,
                    "denial_reason": (DENIAL_REASONS[0] if needs_pa else rng.choice(DENIAL_REASONS)) if status in ("denied", "appealed") else None,
                    "submitted_date": submitted,
                    "adjudicated_date": None if status == "pending" else submitted + timedelta(days=rng.randint(7, 40)),
                })

            if "E11.9" in chronic and etype == "outpatient" and rng.random() < 0.6:
                labs.append(_lab(rng, nid("lab", "LAB"), pt, enc, "4548-4", "Hemoglobin A1c", rng.gauss(7.6, 1.3), "%", 4.0, 5.6))
            if rng.random() < 0.5:
                labs.append(_lab(rng, nid("lab", "LAB"), pt, enc, "2160-0", "Creatinine", rng.gauss(1.1 if "N18.30" not in chronic else 1.9, 0.3), "mg/dL", 0.6, 1.3))
                labs.append(_lab(rng, nid("lab", "LAB"), pt, enc, "2823-3", "Potassium", rng.gauss(4.3, 0.5), "mmol/L", 3.5, 5.1))

            for code in ([dx] if etype != "outpatient" else chronic[:2]):
                for drug in DRUGS_BY_DX.get(code, [])[: rng.randint(0, 2)]:
                    rx.append({
                        "rx_id": nid("rx", "RX"), "patient_id": pt["patient_id"], "provider_id": enc["provider_id"],
                        "encounter_id": enc["encounter_id"], "drug_name": drug, "days_supply": rng.choice([30, 90]),
                        "refills": rng.randint(0, 5), "fill_date": enc["discharge_ts"].date() + timedelta(days=rng.randint(0, 3)),
                    })

    out_patients = [{k: v for k, v in p.items() if k != "chronic_conditions"} for p in patients]
    result = {"encounters": encounters, "diagnoses": diagnoses, "claims": claims,
              "prescriptions": rx, "lab_results": labs}
    if incremental_day is None:
        result = {"patients": out_patients, "providers": providers, **result}
    return result


def _lab(rng, lab_id, pt, enc, loinc, name, value, unit, lo, hi):
    return {
        "lab_id": lab_id, "patient_id": pt["patient_id"], "encounter_id": enc["encounter_id"],
        "loinc_code": loinc, "test_name": name, "result_value": round(max(value, 0.1), 2), "unit": unit,
        "ref_low": lo, "ref_high": hi, "collected_ts": enc["admit_ts"] + timedelta(minutes=rng.randint(10, 90)),
    }
