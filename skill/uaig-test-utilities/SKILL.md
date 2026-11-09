---
name: uaig-test-utilities
description: "Work with this repo's Unity AI Gateway (Unity Gateway) test utilities: the deploy-uaig notebooks and their YAML configs (model-services.yaml, service-policies.yaml, permissions.yaml). Use when setting up a new customer test, editing model services, destinations, fallbacks, rate limits, inference tables, service policies (guardrails), or access grants, when deploying or verifying them in a workspace, or when changing the notebooks."
compatibility: Requires databricks CLI (>= v1.20.0). Local dry runs need Python with databricks-sdk (>= 0.161.0) and pyyaml.
metadata:
  version: "0.2.0"
parent: databricks-unity-gateway
---

# Unity AI Gateway test utilities

This repo gives customers Databricks notebooks for setting up and testing Unity AI Gateway. Users describe their setup in YAML and run a notebook. Use the `databricks-unity-gateway` skill for API details not covered here, and `databricks-core` for CLI authentication and profiles.

## Rules

- **Keep the repo customer-agnostic.** The same files are reused for every customer, so never write customer names, people's names, workspace URLs, or IDs into repo files. Use generic examples such as `my_catalog`, `poc-admins`, and `first.last@example.com`. Before finishing, grep changed files for the customer and people names from the conversation.
- **Keep the style simple for end users,** who may not be experienced Databricks developers:
  - One notebook per task, configured by YAML files in the same folder.
  - Helpers live inside the notebook, not in separate modules.
  - Every function has a Google-style docstring (Args, Returns, Raises).
  - Comments and markdown are short and plain.
  - When behavior changes, update the YAML comments, the notebook's intro markdown, and `README.md`.
- **Confirm before changing a workspace.** Ask before running a notebook, or creating, updating, or granting anything. Deleting a service and revoking access each need their own explicit confirmation. Read-only checks (`get`, `list`, `get-effective`) don't need confirmation.

## Layout

| File | Purpose |
|---|---|
| `deploy-uaig/model-services.yaml` | Global `catalog`/`schema`, default `rate_limits`, and `model_services`. Each service has a name, description, destinations (with optional traffic split), fallbacks, inference table and rate limit switches, and policy names. No access settings. |
| `deploy-uaig/service-policies.yaml` | `default_evaluator` (`system.ai.gpt-oss-20b`) and every policy definition, built-in and custom, with its behavior settings. |
| `deploy-uaig/deploy_model_service.py` | Creates or updates services, creates SQL policy functions, and attaches policies. It doesn't grant access. |
| `deploy-uaig/permissions.yaml` | `groups` and `users`, each with `model_services` (three-part names) and optional `privileges` (default `EXECUTE`). |
| `deploy-uaig/grant_permissions.py` | Grants `permissions.yaml`, plus `USE CATALOG` and `USE SCHEMA` on the parents. It only adds access, never revokes. |
| `README.md` | User-facing guide. Keep it in sync with the configs. |
| `skill/uaig-test-utilities/scripts/dry_run.py` | Runs a notebook locally with a fake client and prints the API calls it would make. |

`permissions.yaml` and `grant_permissions` are the only place access is granted. Don't add access settings to `model-services.yaml` or grant calls to `deploy_model_service`. Run `grant_permissions` after `deploy_model_service`. Until then, only the service's creator can use a new service.

## How deploy_model_service works

1. Creates or replaces a Unity Catalog function `<catalog>.<schema>.<policy name>(event VARIANT) RETURNS VARIANT` for every `sql` policy. Each SQL function is attached by its function ID, and `CREATE OR REPLACE` can give the function a new ID. That's why the notebook re-attaches policies on every run.
2. For each service:
   - Builds the request body and the policy list first, so a config error stops before anything changes.
   - Looks the service up with `GET /api/2.1/unity-catalog/model-services/<full_name>`.
   - If the service exists, sends `PATCH` with `update_mask=comment,config.routing.destinations,config.routing.fallback.destinations,config.rate_limits,config.inference_table`.
   - Otherwise, sends `POST /api/2.1/unity-catalog/model-services?parent=schemas/<c>.<s>&model_service_id=<name>`.
3. Sets policies with `PATCH ...?update_mask=config.service_policies`. It skips this only when the YAML lists no policies and the service has none attached.

`grant_permissions` grants with `w.grants.update`, using securable types `catalog`, `schema`, and `model_service`.

The notebook calls `w.api_client.do` instead of `w.ai_gateway`, because the SDK's `ModelService` type silently drops `config.service_policies`.

### Service policy wire format

As of 2026-11, service policies are in Beta and attaching them is officially UI-only. Public REST, SDK, and Terraform support is planned for GA, targeted for mid-December 2026, possibly January. The notebook writes the `config.service_policies` field that the UI writes. When the official API ships, update `set_service_policies` and the Beta notes in the notebook and `README.md`.

Every option value is a string.

```json
[
  {"name": "jailbreak_detection", "policy_type": "POLICY_TYPE_BUILTIN", "handler": "system.ai.block_jailbreak", "rank": 1,
   "options": {"phases": "pre_call", "dry_run": "false", "max_turns": "10", "model_service": "model-services/system.ai.gpt-oss-20b"}},
  {"name": "pii_redaction", "policy_type": "POLICY_TYPE_BUILTIN", "handler": "system.ai.detect_sensitive_data", "rank": 2,
   "options": {"phases": "pre_call,post_call", "dry_run": "false", "action": "transform", "categories": "class.credit_card,class.us_ssn"}},
  {"name": "block_regulated_advice", "policy_type": "POLICY_TYPE_BUILTIN", "handler": "system.ai.invoke_llm_judge", "rank": 3,
   "options": {"phases": "pre_call", "dry_run": "true", "max_turns": "10", "model_service": "model-services/system.ai.gpt-oss-20b",
               "action": "block", "instruction": "Flag requests that ..."}},
  {"name": "block_confidential_codenames", "policy_type": "POLICY_TYPE_CUSTOM",
   "handler": "functions/my_catalog.ai_gateway.block_confidential_codenames", "rank": 4}
]
```

- `phases`: input is `pre_call`, output is `post_call`.
- `dry_run: "true"` means log mode.
- `max_turns` is the conversation window.
- `model_service` is the evaluator.
- `detect_sensitive_data` `action` accepts only `ask`, `block`, or `transform`. The UI's "redact" is `transform`, and `ask` is for MCP services. The YAML uses `block` and `redact`, and the notebook converts `redact` to `transform` (`SENSITIVE_DATA_ACTIONS`).
- Custom LLM judges are `POLICY_TYPE_BUILTIN` with the `system.ai.invoke_llm_judge` handler.
- SQL policies take no options and run on both phases.

To see a real example, read any service that has policies attached in the UI with `databricks api get /api/2.1/unity-catalog/model-services/<full_name>`.

### Destination names

A `databricks` destination or fallback must be a **Unity Catalog model**, not a `system.ai` model service name. This is the most common config mistake. Most models carry a `databricks-` prefix that their service name doesn't:

| Model service (don't use as a destination) | Model (use this) |
|---|---|
| `system.ai.claude-opus-5-5` | `system.ai.databricks-claude-opus-5-5` |
| `system.ai.claude-sonnet-5-5` | `system.ai.databricks-claude-sonnet-5-5` |
| `system.ai.kimi-k3-1-pro` | `system.ai.databricks-kimi-k3-1-pro` |
| `system.ai.gpt-6-1-sol` | `system.ai.databricks-gpt-6-1-sol` |
| `system.ai.gpt-oss-120b` | `system.ai.gpt-oss-120b` (no prefix) |

Look the model up rather than guessing, then confirm it exists:

```bash
databricks ai-gateway get-model-service model-services/system.ai.<service> --profile <PROFILE> -o json
# the model is config.routing.destinations[].pay_per_token_config.model, without the "models/" prefix
databricks registered-models get system.ai.<model> --profile <PROFILE>   # fails for a service name
```

Policy evaluators are the opposite. `evaluator` and `default_evaluator` in `service-policies.yaml` take a **model service** name, such as `system.ai.gpt-oss-20b`, because the notebook sends them as `model-services/<name>`.

The notebook names each destination after its model (external destinations use `<provider service>.<external_model>`).

## Workflow: set up a new customer test

1. **Gather inputs**, and ask for all missing ones together:
   - The CLI profile.
   - The catalog and schema.
   - The groups and users.
   - For each service: destinations, any traffic split, fallbacks, rate limits, and which policies.
   - Custom policy rules.

   Don't pick a catalog or schema yourself.
2. **Check that the inputs exist** (read-only):
   ```bash
   databricks schemas get <catalog>.<schema> --profile <PROFILE>
   databricks registered-models get system.ai.<model> --profile <PROFILE>                       # every databricks destination and fallback
   databricks ai-gateway get-model-service model-services/system.ai.<service> --profile <PROFILE>   # every policy evaluator
   databricks ai-gateway get-model-provider-service model-provider-services/<c>.<s>.<svc> --profile <PROFILE>   # external destinations
   ```
   - Groups must be account-level.
   - A workspace admin must turn on the **Unity Gateway Service Policies** preview. You can't check this from the CLI, so ask.
3. **Edit the YAML files.** Keep their comment blocks accurate.
   - Model services are named `<catalog>.<schema>.<name>` in `permissions.yaml`. When you rename or add a service in `model-services.yaml`, update `permissions.yaml` to match, or `grant_permissions` fails for that service.
4. **Dry-run locally.** See below.
5. **Deploy, after the user confirms.** Upload the folder and run the notebook on serverless:
   ```bash
   databricks workspace import-dir deploy-uaig <WORKSPACE_DIR> --overwrite --profile <PROFILE>
   databricks jobs submit --profile <PROFILE> -o json --json '{
     "run_name": "deploy-uaig",
     "tasks": [{"task_key": "deploy",
                "notebook_task": {"notebook_path": "<WORKSPACE_DIR>/deploy_model_service", "source": "WORKSPACE"}}]
   }'
   ```
   - `import-dir` strips the `.py` extension from notebooks.
   - `jobs submit` waits for the run to finish. Check `state.result_state` and share `run_page_url`.
   - Per-service messages are printed in the notebook, so they don't come back through the API. On failure, `databricks jobs get-run-output <tasks[0].run_id>` returns `error` and `error_trace`. The run page shows the full output.
   - Run `grant_permissions` the same way.
6. **Verify** (read-only):
   ```bash
   databricks ai-gateway get-model-service model-services/<c>.<s>.<svc> --profile <PROFILE> -o json   # routing, rate_limits, inference_table
   databricks api get /api/2.1/unity-catalog/model-services/<c>.<s>.<svc> --profile <PROFILE>        # includes config.service_policies
   databricks grants get-effective model_service <c>.<s>.<svc> --principal <group> --profile <PROFILE>
   databricks api post /ai-gateway/mlflow/v1/responses --profile <PROFILE> \
     --json '{"model": "<c>.<s>.<svc>", "input": "What is Databricks?", "max_output_tokens": 256}'
   ```
   - A request blocked by a policy still returns HTTP 200, with a `databricks_service_policy` object that holds the `reason`.
   - Grant changes can take a few minutes to take effect.

## Dry run

The dry run runs a notebook's real code with a fake `WorkspaceClient` and fake `spark`. It prints every SQL statement, API call, and grant without contacting a workspace. Use it after every config or code change.

```bash
python3 -m venv /tmp/uaig-venv && /tmp/uaig-venv/bin/pip install -q "databricks-sdk>=0.161.0" pyyaml
/tmp/uaig-venv/bin/python skill/uaig-test-utilities/scripts/dry_run.py deploy-uaig/deploy_model_service.py
/tmp/uaig-venv/bin/python skill/uaig-test-utilities/scripts/dry_run.py deploy-uaig/deploy_model_service.py --existing <catalog>.<schema>.<service>
/tmp/uaig-venv/bin/python skill/uaig-test-utilities/scripts/dry_run.py deploy-uaig/grant_permissions.py
```

`--existing` treats the named services as already existing, so the dry run shows the update path. The dry run doesn't prove the SQL in a `sql` policy is valid. To test that expression, run it in a read-only `SELECT` on a SQL warehouse against sample events, e.g. `SELECT (<expression>) FROM (SELECT parse_json('{"type":"request","context":{"message":"..."}}') AS event)`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Creating or updating a service fails with a destination that doesn't exist | The destination is a model service name, not a model. See [Destination names](#destination-names). For most models, add the `databricks-` prefix. |
| `PERMISSION_DENIED` creating a service | The runner lacks `CREATE SERVICE` or `USE SCHEMA` on the schema, `USE CATALOG` on the catalog, or `EXECUTE` on a destination. |
| `grant_permissions` fails with a model service that doesn't exist | `permissions.yaml` still uses an old service name, or `deploy_model_service` hasn't run yet. |
| The `config.service_policies` PATCH fails | The Service Policies preview is off, or the API changed at GA. Check the service's **Policies** tab in the AI Gateway UI. |
| `policy function '<name>' SQL cannot be transpiled to CEL` | The SQL policy uses something unsupported, such as `ai_query`, subqueries, `BETWEEN`, aggregates, or a `COALESCE`/`CONCAT` with more than two arguments. |
| A SQL policy denies everything | A comparison on a missing field fails closed. Wrap fields in `CAST(COALESCE(event:<path>, '') AS STRING)`. |
| A grant to a group fails | The group is workspace-local. Use an account-level group. |
| An update fails on the inference table | The table's location can't change after creation. Deleting and recreating the service needs explicit confirmation. |
| Streamed responses arrive all at once | An output-phase policy is attached, so the gateway waits for the full response. |
| Requests are denied unexpectedly | Policies fail closed when a check errors, for example when an evaluator times out on a very large prompt. Use `mode: log` while testing. |
