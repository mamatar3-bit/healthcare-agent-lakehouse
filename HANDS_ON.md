# Hands-on lab guide

Twelve labs that build the project up layer by layer. Each lab has **Do**, **Check** (what you should
see) and **Try** (a small change to make yourself). Plan on roughly a day for all of them; Labs 1–6
alone give you the whole data side.

**You need:** a Databricks workspace with Unity Catalog, serverless compute, a SQL warehouse, Vector Search
and Genie; the Databricks CLI (`databricks -v` ≥ 0.250); Python 3.11; Git; a GitHub account for Lab 12.

Throughout, `<wh>` is your SQL warehouse ID (SQL Warehouses → your warehouse → Connection details).

---

## Lab 1 · Get the code into Git

**Do**
```bash
unzip healthcare-agent-lakehouse.zip && cd healthcare-agent-lakehouse
git log --oneline            # the repo already has an initial commit
gh repo create healthcare-agent-lakehouse --private --source . --push   # or create it on github.com
git switch -c feature/lab-work
```
**Check** `git status` is clean and you're on `feature/lab-work`.

**Try** Read `.gitignore`: why are `dbt/target/` and `profiles.yml` excluded?

---

## Lab 2 · Run the tests locally (no workspace needed)

**Do**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check agent src tests
```
**Check** `41 passed`. `tests/test_agent_graph.py` runs the *real* LangGraph agent with fake Genie, UC
functions and retriever, so you can see routing, fan-out and fallback without spending a token.

**Try** Run one test verbosely and read it: `pytest -q tests/test_agent_graph.py::test_genie_failure_falls_back_to_documents -vv`.

---

## Lab 3 · Deploy your sandbox with the bundle

**Do**
```bash
databricks auth login --host https://<your-workspace>
databricks bundle validate -t personal --var="warehouse_id=<wh>"
databricks bundle deploy   -t personal --var="warehouse_id=<wh>"
```
**Check** In the workspace, Jobs shows `[dev <you>] hc_agent_end_to_end_personal` and the daily job,
paused. Open the job graph and match it to the diagram in the README.

**Try** Change `n_patients` in `src/01_generate_bronze.py`'s widget default to `500`, redeploy, and note
that only the changed file is re-uploaded.

---

## Lab 4 · Bronze data

**Do** In the workspace, open the bundle folder (Workspace → Users → you → `.bundle/healthcare_agent_lakehouse/personal/files/src`),
open `00_setup.py` and then `01_generate_bronze.py`, set the `catalog` widget to `hc_sandbox`, and run
each one. Running notebooks one at a time like this is how you'll work through Labs 4–7.

**Check** in the SQL editor:
```sql
USE CATALOG hc_sandbox;
SELECT encounter_type, count(*) FROM bronze.encounters GROUP BY ALL;
SELECT claim_status, count(*) FROM bronze.claims GROUP BY ALL;
```
**Try** Set `incremental=true` and rerun `01_generate_bronze`. Find the new rows: their IDs contain
today's date (`ENC-2026…`).

---

## Lab 5 · dbt: staging, marts, tests, docs

**Do** (locally, against your sandbox)
```bash
cp dbt/profiles.example.yml ~/.dbt/profiles.yml
export DATABRICKS_HOST=https://<your-workspace> DATABRICKS_TOKEN=<pat> DATABRICKS_WAREHOUSE_ID=<wh> HC_CATALOG=hc_sandbox
cd dbt && dbt deps && dbt seed && dbt build
dbt docs generate && dbt docs serve
```
**Check** `dbt build` ends with all models and tests passing. In Catalog Explorer, open
`hc_sandbox.gold.fct_encounters`: the column comments came from `_marts.yml` (`persist_docs`).
Genie uses these comments.

**Try**
1. Add a model `gold/agg_ed_visits_by_age_band.sql` (ED visits per month by age band, joining
   `fct_encounters` to `dim_patient_deid`), document it in `_marts.yml`, add a `not_null` test, `dbt build -s +agg_ed_visits_by_age_band`.
2. Break a test on purpose: change `accepted_values` for `encounter_type` to drop `telehealth`, run
   `dbt test`, read the failure, revert.
3. `fct_claims` is incremental. Run Lab 4's incremental load, then `dbt run -s fct_claims` and compare
   row counts before and after.

---

## Lab 6 · UC functions, SQL functions and stored procedures

**Do** Run the `uc_functions`, `sql_objects` and `run_procedures` tasks (or the corresponding notebooks
with `catalog=hc_sandbox`, `warehouse_id=<wh>`). Then open `sql/examples.sql` in the SQL editor,
change the first line to `USE CATALOG hc_sandbox;` and run it statement by statement.

**Check**
- `SELECT gold.fn_lace_points(5, TRUE, 2, 1)` and `SELECT ai.lace_score(5, TRUE, 2, 1)` both give 10.
- `ops.care_management_worklist` has rows with readable `eligibility_reasons`.
- `ops.appeal_queue` is ordered by priority, and `sp_log_appeal` writes to `ops.appeal_events`.

**Try**
1. Call `sp_log_appeal` with action `'approve'` and read the `SIGNAL` error.
2. Add a check to `sp_run_data_quality_checks`: claims with `submitted_date` before the encounter's
   `admit_date`. Rerun `08_run_sql` with `sql_files=03_procedures.sql`, then call the procedure.
3. Write `gold.fn_top_denial_reasons(target_payer_id STRING, top_n INT)` as a table function in
   `sql/02_functions.sql`.

---

## Lab 7 · Documents and Vector Search

**Do** Run `03_build_vector_index` (first run creates the endpoint, which takes several minutes).

**Check** The last cell prints the appeals policy's deadline section as the top hit for the Medicare
Advantage question. Look at `hc_sandbox.ai.policy_chunks`: one row per policy section.

**Try** Write a new policy, `docs/policies/interpreter_services_policy.md`, with `##` sections
(when to offer an interpreter, how to request one, documentation). Redeploy, rerun the task, and confirm
the new chunks are in the index. You'll ask the agent about it in Lab 9.

---

## Lab 8 · Genie space

**Do** Follow `genie/space_setup.md` with your sandbox catalog: add the listed tables, paste the general
instructions, sample questions and trusted SQL.

**Check** Ask the space: *"What is the 30-day readmission rate by facility for the last 6 months?"*
and open the generated SQL. It should sum readmissions and index admissions rather than averaging rates.

**Try** Ask *"Show me patient names with the most admissions"*. Genie can't, because names aren't in the
space. That is the HIPAA minimum-necessary design at work. Copy the space ID from its URL for the next lab.

---

## Lab 9 · The agent, interactively

**Do** In the workspace, create a notebook next to `agent/agent.py`:
```python
%pip install -U -qqq "mlflow[databricks]>=3.4" databricks-langchain "unitycatalog-langchain[databricks]" langgraph
%restart_python
```
Edit `agent/config.yaml`: `catalog: hc_sandbox`, replace `hc_dev` with `hc_sandbox` in the index and
function names, and paste your `genie_space_id`. Then:
```python
from agent import AGENT
from mlflow.types.responses import ResponsesAgentRequest

def ask(q, **custom):
    r = AGENT.predict(ResponsesAgentRequest(input=[{"role": "user", "content": q}], custom_inputs=custom))
    print(r.output[0].content[0]["text"]); print(r.custom_outputs)

ask("What is the denial rate by payer this year?")                       # genie
ask("Does Summit PPO require prior auth for 72148?")                     # uc_functions
ask("How long do we have to appeal a Medicare Advantage denial?")        # documents
ask("Claim CLM-0000017 was denied, what should we do next?")             # uc + documents
ask("what about interpreters?")                                          # low confidence -> LLM router
ask("Should I double my metformin?")                                     # guardrail
```
**Check** Each `custom_outputs` shows the route and whether rules or the LLM chose it. Open the MLflow
**Traces** tab to see each node, tool call and retrieval.

**Try**
1. Force a route: `ask("What is the status of claim CLM-0000017?", force_route="documents")` and watch
   the fallback kick in when the docs can't answer.
2. Set `routing_mode: llm` in the config and compare routes and latency for the same questions.
3. Add a routing rule in `agent/routing.py` so "interpreter" questions go straight to documents, add a
   case to `tests/test_routing.py`, run `pytest`.

---

## Lab 10 · Add a new tool end to end

Goal: the agent can answer *"Which labs are abnormal for PAT-000123?"*

1. In `sql/02_functions.sql`, add `ai.get_abnormal_labs(target_patient_id STRING) RETURNS STRING`
   reading `gold.fct_lab_results` (latest abnormal result per test). Write a precise `COMMENT`: it's
   what the LLM reads.
2. Add the function to `uc_function_names` in `agent/config.yaml`.
3. Add a question with `"expected_route": "uc_functions"` to `eval/eval_set.jsonl`.
4. Rerun `sql_objects`, then `ask(...)` it in your Lab 9 notebook.

**Check** The answer cites `UC function get_abnormal_labs` in its sources.

---

## Lab 11 · Evaluate, gate and deploy

**Do** Run the whole job: `databricks bundle run -t personal --var="warehouse_id=<wh>" --var="genie_space_id=<id>" hc_agent_end_to_end`.

**Check**
- `log_and_evaluate` prints routing accuracy and quality scores; the MLflow run has an **Evaluations** tab.
- `eval_gate` is green, `deploy_agent` creates `hc-agent-sandbox`, and `smoke_test` passes.
- The model is in `hc_sandbox.ai.healthcare_router_agent` with aliases `candidate` and `champion`.

**Try** Make the gate fail on purpose: set `eval_pass_threshold` to `0.99` and rerun. The condition task
goes down the false branch, and deploy and smoke test are skipped, which is exactly what protects QA.

---

## Lab 12 · CI/CD: Dev → QA from Git

**Do**
1. Complete the README's **One-time setup** (service principals, GitHub Environments `dev`/`qa`/`prod`
   with their variables and secrets, required reviewers on `qa` and `prod`, branch protection on `main`).
2. Commit your lab changes and open a pull request:
   ```bash
   git add -A && git commit -m "Lab work: new mart, DQ check, abnormal labs tool"
   git push -u origin feature/lab-work
   gh pr create --fill
   ```
3. Watch **CI** run lint, tests, `dbt parse --warn-error` and `bundle validate`. Merge when green.
4. On `main`, **CD** deploys to Dev and runs the job. When it succeeds, `promote-qa` waits for approval.
   Approve it in the Actions run. The *same commit* deploys to QA, runs with the 0.90 gate, and gets a
   `qa-…` tag.
5. Release to prod: `git tag v1.0.0 <qa-tagged-sha> && git push origin v1.0.0`.

**Check** Dev and QA jobs are owned by the service principal, not you; QA objects live in `hc_qa`; a tag
on a commit without a `qa-*` tag is refused by `release-prod.yml`.

**Try** Open a PR that breaks a routing test. CI blocks the merge, and nothing reaches Dev.

---

## Where to go next

- Move the agent to **Databricks Apps** with on-behalf-of-user auth so Genie and UC functions run as the
  person asking.
- Add **Lakehouse Monitoring** on `fct_claims` and an inference-table dashboard for the endpoint.
- Replace the synthetic generator with a real source (FHIR bulk export via Auto Loader into bronze) in a
  HIPAA-compliant workspace.
