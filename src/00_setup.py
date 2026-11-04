# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup catalog, schemas and volume
# MAGIC Creates the medallion layout for this environment (`dev`, `qa`, `prod` each use their own catalog).
# MAGIC
# MAGIC | schema | contents |
# MAGIC |---|---|
# MAGIC | `bronze` | raw synthetic EHR/claims extracts |
# MAGIC | `ref` | dbt seeds: ICD-10, CPT, payers, formulary, prior-auth rules, drug interactions |
# MAGIC | `silver` | dbt staging models |
# MAGIC | `gold` | dbt marts that Genie and the UC functions query |
# MAGIC | `ai` | UC functions (agent tools), document chunks, vector index, registered agent model |
# MAGIC | `ops` | care-management worklist, appeal queue, data-quality results and the stored procedures |

# COMMAND ----------

dbutils.widgets.text("catalog", "hc_dev")
catalog = dbutils.widgets.get("catalog")

# COMMAND ----------

spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
for schema, comment in [
    ("bronze", "Raw synthetic source extracts (no real PHI)"),
    ("ref", "Reference data loaded by dbt seeds"),
    ("silver", "dbt staging models: cleaned, typed, deduplicated"),
    ("gold", "dbt marts: analytics-ready facts and dimensions"),
    ("ai", "Agent tools, document chunks, vector index and agent models"),
    ("ops", "Operational tables, stored procedures and data quality results"),
]:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema} COMMENT '{comment}'")

spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.ai.policy_docs COMMENT 'Source policy documents for RAG'")
print(f"Ready: {catalog}.{{bronze, ref, silver, gold, ai, ops}}")
