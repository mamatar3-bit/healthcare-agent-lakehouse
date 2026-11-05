# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Deploy the agent
# MAGIC Runs only when the eval gate passed. Deploys the registered version to a Model Serving endpoint
# MAGIC with the Agent Framework (Review App, inference tables, tracing) and moves the `champion` alias.
# MAGIC
# MAGIC Databricks now recommends Databricks Apps for new agents; Model Serving is used here because it
# MAGIC deploys a registered UC model from a job in one call. Switching later only changes this task.

# COMMAND ----------

# MAGIC %pip install -U -qqq "mlflow[databricks]>=3.4" databricks-agents
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("catalog", "hc_dev")
dbutils.widgets.text("serving_endpoint_name", "hc-agent-dev")
dbutils.widgets.text("env", "dev")
catalog = dbutils.widgets.get("catalog")
endpoint = dbutils.widgets.get("serving_endpoint_name")
env = dbutils.widgets.get("env")
UC_MODEL = f"{catalog}.ai.healthcare_router_agent"

version = dbutils.jobs.taskValues.get(taskKey="log_and_evaluate", key="model_version", debugValue="1")

# COMMAND ----------

import time

from databricks import agents
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from mlflow import MlflowClient

# Free Edition (and small quotas) can't run the old and new versions side by side during an
# update, so outside QA/Prod we delete the existing endpoint first and deploy fresh.
if env not in ("qa", "prod"):
    w = WorkspaceClient()
    try:
        w.serving_endpoints.get(endpoint)
        print(f"Deleting existing endpoint {endpoint} to free capacity")
        w.serving_endpoints.delete(endpoint)
        for _ in range(30):
            time.sleep(10)
            w.serving_endpoints.get(endpoint)
    except NotFound:
        pass

deployment = agents.deploy(
    UC_MODEL, int(version),
    endpoint_name=endpoint,
    scale_to_zero=(env != "prod"),  # Free Edition and sandboxes require scale to zero
    tags={"env": env, "project": "healthcare-agent-lakehouse"},
)
MlflowClient(registry_uri="databricks-uc").set_registered_model_alias(UC_MODEL, "champion", int(version))
print(f"Deployed {UC_MODEL} v{version} to {endpoint}")
print("Review App:", getattr(deployment, "review_app_url", "see the endpoint page"))
