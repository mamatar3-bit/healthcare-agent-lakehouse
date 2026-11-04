# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Policy documents → chunks → Vector Search index
# MAGIC 1. Copies `docs/policies/*.md` (deployed with the bundle) into the `policy_docs` volume.
# MAGIC 2. Chunks them by section into `<catalog>.ai.policy_chunks` (Change Data Feed on).
# MAGIC 3. Creates or syncs a Delta Sync index with Databricks-managed embeddings.
# MAGIC
# MAGIC To use your own documents, drop Markdown files into the volume (or swap in `ai_parse_document`
# MAGIC for PDFs) and re-run this task.

# COMMAND ----------

# MAGIC %pip install -U -qqq databricks-vectorsearch
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("catalog", "hc_dev")
dbutils.widgets.text("vector_search_endpoint", "hc_vs_endpoint")
dbutils.widgets.text("embedding_endpoint", "databricks-gte-large-en")
catalog = dbutils.widgets.get("catalog")
vs_endpoint = dbutils.widgets.get("vector_search_endpoint")
embedding_endpoint = dbutils.widgets.get("embedding_endpoint")

volume_dir = f"/Volumes/{catalog}/ai/policy_docs"
chunk_table = f"{catalog}.ai.policy_chunks"
index_name = f"{catalog}.ai.policy_chunks_index"

# COMMAND ----------

import os
import shutil

from chunking import chunk_folder

bundle_docs = os.path.abspath("../docs/policies")
for name in os.listdir(bundle_docs):
    if name.endswith(".md"):
        shutil.copy(os.path.join(bundle_docs, name), os.path.join(volume_dir, name))

rows = chunk_folder(volume_dir)
print(f"{len(rows)} chunks from {len({r['doc_name'] for r in rows})} documents")

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {chunk_table} (
  chunk_id STRING NOT NULL, doc_name STRING, doc_title STRING, section STRING,
  chunk_index INT, content STRING
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
COMMENT 'Section-level chunks of network policy documents for retrieval'
""")

spark.createDataFrame(rows).createOrReplaceTempView("new_chunks")
spark.sql(f"""
MERGE INTO {chunk_table} t USING new_chunks s ON t.chunk_id = s.chunk_id
WHEN MATCHED AND t.content <> s.content THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE THEN DELETE
""")

# COMMAND ----------

import time

from databricks.vector_search.client import VectorSearchClient

vsc = VectorSearchClient(disable_notice=True)

if vs_endpoint not in [e["name"] for e in vsc.list_endpoints().get("endpoints", [])]:
    vsc.create_endpoint_and_wait(name=vs_endpoint, endpoint_type="STANDARD")

existing = [i["name"] for i in vsc.list_indexes(name=vs_endpoint).get("vector_indexes", [])]
if index_name not in existing:
    vsc.create_delta_sync_index_and_wait(
        endpoint_name=vs_endpoint,
        index_name=index_name,
        source_table_name=chunk_table,
        pipeline_type="TRIGGERED",
        primary_key="chunk_id",
        embedding_source_column="content",
        embedding_model_endpoint_name=embedding_endpoint,
    )
else:
    index = vsc.get_index(endpoint_name=vs_endpoint, index_name=index_name)
    index.sync()
    for _ in range(90):  # up to 15 minutes
        state = index.describe().get("status", {}).get("detailed_state", "")
        if state == "ONLINE_NO_PENDING_UPDATE":
            break
        time.sleep(10)
    print("Index state:", state)

# COMMAND ----------

index = vsc.get_index(endpoint_name=vs_endpoint, index_name=index_name)
hits = index.similarity_search(
    query_text="How long do I have to appeal a Medicare Advantage denial?",
    columns=["doc_name", "section"], num_results=3,
)
for row in hits["result"]["data_array"]:
    print(row)
