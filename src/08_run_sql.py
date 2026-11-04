# Databricks notebook source
# MAGIC %md
# MAGIC # 08 · Run SQL files on the SQL warehouse
# MAGIC Deploys and runs the `sql/` folder: ops tables, SQL functions, stored procedures and the daily
# MAGIC procedure run. Statements go through the SQL warehouse (Statement Execution API) because stored
# MAGIC procedures and SQL scripting need Databricks SQL or DBR 17+.
# MAGIC
# MAGIC `${catalog}` in each file is replaced with this environment's catalog; statements are split on
# MAGIC lines that read `-- @@`.

# COMMAND ----------

dbutils.widgets.text("catalog", "hc_dev")
dbutils.widgets.text("warehouse_id", "")
dbutils.widgets.text("sql_files", "01_ops_tables.sql,02_functions.sql,03_procedures.sql")
catalog = dbutils.widgets.get("catalog")
warehouse_id = dbutils.widgets.get("warehouse_id")
files = [f.strip() for f in dbutils.widgets.get("sql_files").split(",") if f.strip()]

# COMMAND ----------

import os
import re
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

w = WorkspaceClient()
sql_dir = os.path.abspath("../sql")


def statements(path: str) -> list[str]:
    text = open(path, encoding="utf-8").read().replace("${catalog}", catalog)
    parts = re.split(r"^\s*--\s*@@.*$", text, flags=re.M)
    out = []
    for p in parts:
        code = p.strip()
        if re.sub(r"--[^\n]*", "", code).strip():
            out.append(code.rstrip().rstrip(";"))
    return out


def run(stmt: str):
    resp = w.statement_execution.execute_statement(statement=stmt, warehouse_id=warehouse_id, wait_timeout="50s")
    while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(3)
        resp = w.statement_execution.get_statement(resp.statement_id)
    if resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"SQL failed: {err}\n---\n{stmt[:800]}")
    return resp


for f in files:
    for i, stmt in enumerate(statements(os.path.join(sql_dir, f)), start=1):
        first_line = next(ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--"))
        print(f"[{f} #{i}] {first_line[:110]}")
        resp = run(stmt)
        if resp.result and resp.result.data_array:
            cols = [c.name for c in resp.manifest.schema.columns]
            for row in resp.result.data_array[:20]:
                print("   ", dict(zip(cols, row, strict=False)))
