# Unity AI Gateway test utilities

Databricks notebooks for setting up and testing Unity AI Gateway (also called Unity Gateway). You describe the setup in YAML files, and the notebooks create it in your workspace.

## What's in this repo

| File | Purpose |
|---|---|
| `deploy-uaig/model-services.yaml` | The model services to create: destinations, fallbacks, rate limits, inference tables, and service policies. |
| `deploy-uaig/service-policies.yaml` | Guardrails (built-in and custom) that model services can use. |
| `deploy-uaig/deploy_model_service.py` | Notebook that creates or updates the model services and attaches their service policies. |
| `deploy-uaig/permissions.yaml` | Which groups and users can use which model services. |
| `deploy-uaig/grant_permissions.py` | Notebook that grants the access listed in `permissions.yaml`. |

## Before you start

- A Unity Catalog catalog and schema for the model services.
- Account-level groups for the people who will use the services.
- Serverless compute, or a cluster on Databricks Runtime 15.3 or above.
- To use service policies, a workspace admin must turn on **Unity Gateway Service Policies** under **Settings > Previews**.

The person who runs the notebooks needs:

| To | Privileges |
|---|---|
| Create model services | `USE CATALOG` on the catalog. `USE SCHEMA`, `CREATE SERVICE`, `CREATE TABLE`, and `CREATE FUNCTION` on the schema. |
| Route traffic and run LLM checks | `EXECUTE` on every destination and fallback model, and on every policy evaluator model. |
| Update services | `MANAGE` or ownership on the model services. |
| Grant access (`grant_permissions`) | `MANAGE` or ownership on the model services, and on their catalogs and schemas. |

## Set up

1. Add this repo to your workspace as a Git folder (**Workspace > Create > Git folder**), or import the `deploy-uaig` folder. Keep the YAML files in the same folder as the notebooks.
2. In `model-services.yaml`, set `catalog` and `schema`, and list your model services.
3. In `service-policies.yaml`, define the guardrails your services use.
4. In `permissions.yaml`, list who can use each service.
5. Run the notebooks. See [How to Run](#how-to-run).

Each YAML file has comments that explain every setting.

### Model services

```yaml
catalog: my_catalog
schema: ai_gateway

model_services:
  - name: poc_developers_chat
    description: Chat model for PoC developers.
    destinations:                    # with more than one, traffic_percentage must add up to 100
      - provider: databricks
        destination: system.ai.gpt-oss-120b
        traffic_percentage: 70
      - provider: databricks
        destination: system.ai.databricks-claude-sonnet-4-6
        traffic_percentage: 30
    fallbacks:                       # tried in order when a destination fails
      - provider: databricks
        destination: system.ai.gpt-oss-20b
    enable_inference_table: true
    enable_rate_limits: true
    service_policies: [jailbreak_detection, block_confidential_codenames]
```

| `provider` | `destination` |
|---|---|
| `databricks` | A Databricks-hosted model in `system.ai`. Names don't follow one pattern (for example, `system.ai.databricks-claude-sonnet-4-6` and `system.ai.gpt-oss-120b`), so copy them exactly from Catalog Explorer. |
| `provisioned_throughput` | A Model Serving endpoint name. |
| `external` | A model provider service (`catalog.schema.service`). Also set `external_model` to the provider's model name. Create the model provider service first. |

### Service policies

Define each policy once in `service-policies.yaml`, then list it by name under a model service's `service_policies`. Policies run in the order listed.

| `type` | What it does | Uses an LLM |
|---|---|---|
| `block_unsafe_content` | Blocks unsafe or harmful content. | Yes |
| `block_jailbreak` | Blocks jailbreak and prompt-injection attempts (input only). | Yes |
| `block_hallucination` | Blocks hallucinated responses (output only). | Yes |
| `detect_sensitive_data` | Blocks or redacts data such as credit card and Social Security numbers. | No |
| `llm_judge` | Custom rule written in plain language. | Yes |
| `sql` | Custom rule written in SQL. The notebook creates it as a Unity Catalog function. | No |

Checks that use an LLM run on `system.ai.gpt-oss-20b` by default. Change `default_evaluator`, or set `evaluator` on a single policy. Set `mode: log` to record what a policy would decide without blocking anything.

```yaml
default_evaluator: system.ai.gpt-oss-20b

service_policies:
  - name: jailbreak_detection
    type: block_jailbreak

  - name: pii_redaction
    type: detect_sensitive_data
    action: redact
    categories: [credit_card, us_ssn]
```

### Permissions

```yaml
groups:
  - name: poc-admins
    model_services:
      - my_catalog.ai_gateway.poc_developers_chat
    privileges: [EXECUTE, MANAGE]

users:
  - name: first.last@example.com
    model_services:
      - my_catalog.ai_gateway.poc_developers_chat
```

This is the only place to grant access to model services. Services can be in any catalog and schema, including ones this repo didn't create.

Set `privileges` to any of these. If you leave it out, the default is `EXECUTE`.

| Privilege | What it allows on a model service |
|---|---|
| `EXECUTE` | Query the service. Most users need only this. |
| `READ_METADATA` | View the service's settings and permissions without querying it. |
| `APPLY_TAG` | Add and edit tags on the service. |
| `MANAGE` | Change or delete the service and control who can access it. Give it only to people who administer the service. |
| `ALL_PRIVILEGES` | Every privilege except `MANAGE` and `READ_METADATA`. |

Names can also be written with spaces, such as `READ METADATA`. The notebook also grants `USE CATALOG` and `USE SCHEMA` on each service's catalog and schema, which everyone needs to reach a service.

## How to Run

Run `deploy_model_service` first, then `grant_permissions`. A model service has to exist before you can grant access to it, and until you do, only the person who created it can use it. To change who has access, edit `permissions.yaml` and rerun only `grant_permissions`.

### From the Databricks UI

1. Open the `deploy-uaig` folder in your workspace.
2. Open the `deploy_model_service` notebook, connect it to **Serverless** compute (or a cluster on Databricks Runtime 15.3 or above), and click **Run all**.
3. Check the output of the last cell. Each service shows `Created` or `Updated`, followed by its service policies.
   If a service fails, it shows `FAILED` with the reason, and the cell ends with an error that lists the failed services. Fix the YAML and run the notebook again.
4. Open the `grant_permissions` notebook and click **Run all**. Each group or user shows the access it was granted, or `FAILED` with the reason.

### From the command line

This uses the [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/) (v1.20.0 or above) with a profile for your workspace.

```bash
# Upload the folder. Notebooks lose their .py extension in the workspace.
databricks workspace import-dir deploy-uaig /Workspace/Users/<you>/deploy-uaig --overwrite --profile <PROFILE>

# Run the deploy notebook on serverless compute and wait for it to finish.
databricks jobs submit --profile <PROFILE> --json '{
  "run_name": "deploy-uaig",
  "tasks": [{
    "task_key": "deploy",
    "notebook_task": {"notebook_path": "/Workspace/Users/<you>/deploy-uaig/deploy_model_service", "source": "WORKSPACE"}
  }]
}'
```

Run `grant_permissions` the same way, with `grant_permissions` at the end of `notebook_path`. Each command prints the run's result and a `run_page_url`. Open that link to see the notebook output. After you edit a YAML file, upload the folder again before you rerun.

### Running again

- `deploy_model_service` updates existing services to match the YAML: description, destinations, fallbacks, rate limits, inference table, and service policies. Taking a policy out of a service's list removes it from the service.
- An inference table's location can't change after it's created.
- `grant_permissions` only adds access. Removing a group or user from `permissions.yaml` doesn't revoke its access.

## Try it out

Open **AI Gateway** in the workspace sidebar, select a model service, and send it a few prompts. Or query it from Python with the OpenAI client:

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DATABRICKS_TOKEN"],
    base_url=f"{os.environ['DATABRICKS_HOST'].rstrip('/')}/ai-gateway/mlflow/v1",
)

response = client.responses.create(
    model="my_catalog.ai_gateway.poc_developers_chat",
    input=[{"role": "user", "content": "What is Databricks?"}],
)
print(response.output_text)
```

- When a policy blocks a request, the response is still HTTP 200. It contains a short assistant message and a `databricks_service_policy` object with the reason.
- Requests and responses are logged to the inference table `<catalog>.<schema>.<service name>_payload`.

## Known limitations

- Service policies are in Beta, and Databricks hasn't yet published an API for attaching them. `deploy_model_service` uses the model service API field that the AI Gateway UI uses, which may change when the feature is generally available.
- Output checks wait for the full response, so streamed responses arrive all at once.
- If a policy check fails (for example, the evaluator times out), the request is denied. Use `mode: log` while you test.
