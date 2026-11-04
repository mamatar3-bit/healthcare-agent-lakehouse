# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Log, evaluate and register the agent
# MAGIC 1. Builds this environment's agent config (catalog, Genie space, endpoints, UC functions).
# MAGIC 2. Logs `agent/agent.py` with MLflow "models from code", declaring every Databricks resource it
# MAGIC    uses so the serving endpoint gets automatic credentials.
# MAGIC 3. Evaluates on `eval/eval_set.jsonl`: **routing accuracy** (the gate) plus LLM-judge quality scores.
# MAGIC 4. Registers the model in Unity Catalog and sets `eval_passed` for the job's condition task.

# COMMAND ----------

# MAGIC %pip install -U -qqq "mlflow[databricks]>=3.4" databricks-langchain "unitycatalog-langchain[databricks]" langgraph databricks-agents
# MAGIC %restart_python

# COMMAND ----------

for k, v in {"catalog": "hc_dev", "llm_endpoint": "databricks-claude-sonnet-4-5", "genie_space_id": "",
             "warehouse_id": "", "eval_pass_threshold": "0.85", "env": "dev"}.items():
    dbutils.widgets.text(k, v)
p = {k: dbutils.widgets.get(k) for k in
     ["catalog", "llm_endpoint", "genie_space_id", "warehouse_id", "eval_pass_threshold", "env"]}
catalog = p["catalog"]
UC_MODEL = f"{catalog}.ai.healthcare_router_agent"

# COMMAND ----------

import json
import os
import sys

import mlflow
import yaml
from mlflow.models.resources import (DatabricksFunction, DatabricksGenieSpace, DatabricksServingEndpoint,
                                     DatabricksSQLWarehouse, DatabricksTable, DatabricksVectorSearchIndex)

agent_dir = os.path.abspath("../agent")
sys.path.insert(0, agent_dir)

base = yaml.safe_load(open(os.path.join(agent_dir, "config.yaml")))
fn_names = [n.split(".")[-1] for n in base["uc_function_names"]]
config = {
    **base,
    "llm_endpoint": p["llm_endpoint"],
    "catalog": catalog,
    "genie_space_id": p["genie_space_id"],
    "vector_search_index": f"{catalog}.ai.policy_chunks_index",
    "uc_function_names": [f"{catalog}.ai.{n}" for n in fn_names],
}
print(json.dumps(config, indent=2))

# COMMAND ----------

gold_tables = ["fct_encounters", "fct_claims", "fct_prescriptions", "fct_lab_results", "dim_patient_deid",
               "dim_provider", "dim_payer", "agg_readmissions_monthly", "agg_monthly_utilization",
               "agg_claim_denials_monthly", "patient_risk_features"]
resources = [
    DatabricksServingEndpoint(endpoint_name=config["llm_endpoint"]),
    DatabricksVectorSearchIndex(index_name=config["vector_search_index"]),
    *[DatabricksFunction(function_name=f) for f in config["uc_function_names"]],
    DatabricksFunction(function_name=f"{catalog}.ai.lace_score"),
    DatabricksFunction(function_name=f"{catalog}.gold.fn_lace_points"),
    *[DatabricksTable(table_name=f"{catalog}.gold.{t}") for t in gold_tables],
    *[DatabricksTable(table_name=f"{catalog}.{t}") for t in
      ["ref.prior_auth_rules", "ref.cpt_codes", "ref.payers", "ref.formulary", "ref.drug_interactions",
       "ops.care_management_worklist", "ops.appeal_queue", "ops.appeal_events"]],
]
if config["genie_space_id"]:
    resources.append(DatabricksGenieSpace(genie_space_id=config["genie_space_id"]))
if p["warehouse_id"]:
    resources.append(DatabricksSQLWarehouse(warehouse_id=p["warehouse_id"]))

user = spark.sql("select current_user()").first()[0]
mlflow.set_experiment(f"/Workspace/Shared/hc_agent_{p['env']}" if p["env"] != "dev" else f"/Users/{user}/hc_agent_dev")

input_example = {"input": [{"role": "user", "content": "What is the status of claim CLM-0000017?"}]}
with mlflow.start_run(run_name=f"hc-router-{p['env']}") as run:
    mlflow.log_params({"env": p["env"], "llm_endpoint": config["llm_endpoint"],
                       "routing_mode": config["routing_mode"], "genie_enabled": bool(config["genie_space_id"])})
    model_info = mlflow.pyfunc.log_model(
        name="agent",
        python_model=os.path.join(agent_dir, "agent.py"),
        code_paths=[os.path.join(agent_dir, "routing.py")],
        model_config=config,
        resources=resources,
        input_example=input_example,
        pip_requirements=[
            "mlflow>=3.4", "databricks-langchain", "unitycatalog-langchain[databricks]",
            "langgraph>=0.3", "pydantic>=2", "pyyaml",
        ],
    )
print(model_info.model_uri)

# COMMAND ----------

# MAGIC %md ## Evaluate

# COMMAND ----------

from mlflow.genai.scorers import ExpectationsGuidelines, RelevanceToQuery, Safety, scorer

eval_rows = [json.loads(line) for line in open(os.path.abspath("../eval/eval_set.jsonl"))]
loaded = mlflow.pyfunc.load_model(model_info.model_uri)


def predict_fn(input):
    return loaded.predict({"input": input})


def route_label(outputs: dict) -> str:
    co = outputs.get("custom_outputs") or {}
    if co.get("guardrail"):
        return f"guardrail:{co['guardrail']}"
    return "+".join(co.get("routes") or [])


@scorer
def route_correct(outputs, expectations) -> bool:
    expected = expectations["expected_route"]
    actual = route_label(outputs)
    if not config["genie_space_id"] and expected == "genie":
        return True  # Genie disabled in this environment: don't penalize
    if "+" in expected:  # multi-route: primary must match and all expected routes present
        return set(expected.split("+")) <= set(actual.split("+")) and actual.split("+")[0] == expected.split("+")[0]
    return actual.split("+")[0] == expected


data = [{"inputs": {"input": [{"role": "user", "content": r["question"]}]},
         "expectations": {"expected_route": r["expected_route"], "guidelines": r["guidelines"]}}
        for r in eval_rows]

with mlflow.start_run(run_id=run.info.run_id):
    results = mlflow.genai.evaluate(
        data=data, predict_fn=predict_fn,
        scorers=[route_correct, ExpectationsGuidelines(), Safety(), RelevanceToQuery()],
    )
print(json.dumps(results.metrics, indent=2, default=str))

# COMMAND ----------

def metric(name):
    for k, v in results.metrics.items():
        if k.startswith(name) and k.endswith("mean"):
            return float(v)
    return None


route_acc = metric("route_correct")
safety = metric("safety")
threshold = float(p["eval_pass_threshold"])
passed = route_acc is not None and route_acc >= threshold and (safety is None or safety >= 0.95)
print(f"routing accuracy={route_acc} safety={safety} threshold={threshold} -> passed={passed}")

with mlflow.start_run(run_id=run.info.run_id):
    mlflow.log_metrics({"gate_routing_accuracy": route_acc or 0.0, "gate_passed": float(passed)})

# COMMAND ----------

# MAGIC %md ## Register in Unity Catalog

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")
mv = mlflow.register_model(model_info.model_uri, UC_MODEL,
                           tags={"env": p["env"], "eval_passed": str(passed).lower(),
                                 "routing_accuracy": f"{route_acc}"})
from mlflow import MlflowClient

if passed:
    MlflowClient().set_registered_model_alias(UC_MODEL, "candidate", mv.version)

dbutils.jobs.taskValues.set(key="eval_passed", value=str(passed).lower())
dbutils.jobs.taskValues.set(key="model_version", value=str(mv.version))
print(f"Registered {UC_MODEL} v{mv.version}; eval_passed={passed}")
