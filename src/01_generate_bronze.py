# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Generate bronze data
# MAGIC Writes synthetic patients, providers, encounters, diagnoses, claims, prescriptions and labs to
# MAGIC `<catalog>.bronze`. Full mode overwrites two years of history; `incremental=true` appends one day
# MAGIC of new activity (used by the daily refresh job) so the dbt incremental models have new rows to merge.

# COMMAND ----------

dbutils.widgets.text("catalog", "hc_dev")
dbutils.widgets.text("incremental", "false")
dbutils.widgets.text("n_patients", "2000")
catalog = dbutils.widgets.get("catalog")
incremental = dbutils.widgets.get("incremental").lower() == "true"
n_patients = int(dbutils.widgets.get("n_patients"))

# COMMAND ----------

from datetime import date, datetime

import pandas as pd
from pyspark.sql import functions as F

from hc_synth import SynthConfig, generate

cfg = SynthConfig(n_patients=n_patients)
tables_exist = spark.catalog.tableExists(f"{catalog}.bronze.encounters")
run_incremental = incremental and tables_exist
data = generate(cfg, incremental_day=date.today() if run_incremental else None)

# COMMAND ----------

loaded_at = datetime.utcnow()
for name, rows in data.items():
    if not rows:
        continue
    df = spark.createDataFrame(pd.DataFrame(rows)).withColumn("_loaded_at", F.lit(loaded_at))
    target = f"{catalog}.bronze.{name}"
    if run_incremental:
        df.write.mode("append").option("mergeSchema", "true").saveAsTable(target)
    else:
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
    print(f"{'appended' if run_incremental else 'wrote'} {len(rows):>7,} rows -> {target}")
