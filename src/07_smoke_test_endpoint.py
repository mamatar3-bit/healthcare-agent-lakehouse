# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Smoke-test the serving endpoint
# MAGIC Waits for the endpoint to be ready, then asks one question per route and checks the route chosen.

# COMMAND ----------

dbutils.widgets.text("serving_endpoint_name", "hc-agent-dev")
dbutils.widgets.text("genie_space_id", "")
endpoint = dbutils.widgets.get("serving_endpoint_name")
genie_enabled = bool(dbutils.widgets.get("genie_space_id").strip())

# COMMAND ----------

import time
from datetime import timedelta

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
w.serving_endpoints.wait_get_serving_endpoint_not_updating(endpoint, timeout=timedelta(minutes=40))

cases = [
    ("What is the status of claim CLM-0000017?", "uc_functions"),
    ("How long do we have to appeal a Medicare Advantage denial?", "documents"),
    ("Should I stop taking my warfarin?", None),
]
if genie_enabled:
    cases.append(("How many inpatient admissions were there last month?", "genie"))

failures = []
for question, expected in cases:
    for attempt in range(3):
        try:
            resp = w.api_client.do("POST", f"/serving-endpoints/{endpoint}/invocations",
                                   body={"input": [{"role": "user", "content": question}]})
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(30)
    routes = (resp.get("custom_outputs") or {}).get("routes", [])
    text = resp["output"][0]["content"][0]["text"]
    ok = (expected is None and not routes) or (expected in routes)
    print(f"{'PASS' if ok else 'FAIL'} [{routes}] {question}\n   -> {text[:200]}\n")
    if not ok:
        failures.append(question)

assert not failures, f"Smoke test failed for: {failures}"
