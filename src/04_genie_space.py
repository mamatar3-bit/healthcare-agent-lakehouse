# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Validate the Genie space
# MAGIC The space is created once per environment by hand (see `genie/space_setup.md`). This task checks that
# MAGIC the configured space exists and answers a smoke-test question. With no `genie_space_id`, it records
# MAGIC that the Genie route is disabled, and the agent falls back to its other routes.

# COMMAND ----------

dbutils.widgets.text("genie_space_id", "")
dbutils.widgets.text("env", "dev")
space_id = dbutils.widgets.get("genie_space_id").strip()
env = dbutils.widgets.get("env")

# COMMAND ----------

from datetime import timedelta

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
genie_enabled = False

if not space_id:
    msg = "No genie_space_id set: the agent will run without the Genie route. See genie/space_setup.md."
    if env in ("qa", "prod"):
        raise ValueError(f"[{env}] {msg}")
    print(msg)
else:
    space = w.genie.get_space(space_id)
    print(f"Genie space: {space.title} ({space_id})")
    reply = w.genie.start_conversation_and_wait(
        space_id, "How many inpatient admissions were there last month?", timeout=timedelta(minutes=5)
    )
    for att in reply.attachments or []:
        if att.query:
            print("SQL:", att.query.query)
        if att.text:
            print("Text:", att.text.content)
    genie_enabled = True

dbutils.jobs.taskValues.set(key="genie_enabled", value=str(genie_enabled).lower())
