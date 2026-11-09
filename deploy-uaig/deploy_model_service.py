# Databricks notebook source
# MAGIC %md
# MAGIC # Deploy Unity AI Gateway model services
# MAGIC
# MAGIC Creates or updates the model services listed in `model-services.yaml` and attaches their service policies
# MAGIC (guardrails) from `service-policies.yaml`. To give groups and users access, run `grant_permissions` afterward.
# MAGIC
# MAGIC **How to use**
# MAGIC 1. Edit `model-services.yaml` and `service-policies.yaml` (in the same folder as this notebook).
# MAGIC 2. Run all cells on serverless compute or Databricks Runtime 15.3 or above.
# MAGIC
# MAGIC **What it does**
# MAGIC - Creates a Unity Catalog function for each `sql` service policy.
# MAGIC - Creates each model service, or updates it if it already exists. An update replaces the service's description,
# MAGIC   destinations, fallbacks, rate limits, inference table, and service policies with what's in the config.
# MAGIC
# MAGIC **You need**
# MAGIC - The **Unity Gateway Service Policies** preview turned on by a workspace admin (Settings > Previews).
# MAGIC - `USE CATALOG` on the catalog, and `USE SCHEMA`, `CREATE SERVICE`, `CREATE TABLE`, and `CREATE FUNCTION` on the schema.
# MAGIC - `MANAGE` (or ownership) on existing services you update.
# MAGIC - `EXECUTE` on each destination and fallback model, and on each policy evaluator (default `system.ai.gpt-oss-20b`).
# MAGIC
# MAGIC **Note:** Service policies are in Beta, and Databricks has not yet published an API for attaching them.
# MAGIC This notebook uses the model service API field that the AI Gateway UI uses, which may change at GA.

# COMMAND ----------

import yaml
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

MODEL_SERVICES_PATH = "model-services.yaml"
SERVICE_POLICIES_PATH = "service-policies.yaml"
MODEL_SERVICES_API = "/api/2.1/unity-catalog/model-services"

# Fields replaced when an existing model service is updated. Service policies are set separately.
UPDATE_MASK = ",".join([
    "comment",
    "config.routing.destinations",
    "config.routing.fallback.destinations",
    "config.rate_limits",
    "config.inference_table",
])

with open(MODEL_SERVICES_PATH) as f:
    config = yaml.safe_load(f)

with open(SERVICE_POLICIES_PATH) as f:
    policy_config = yaml.safe_load(f) or {}

CATALOG = config["catalog"]
SCHEMA = config["schema"]
DEFAULT_EVALUATOR = policy_config.get("default_evaluator")
service_policies = {p["name"]: p for p in policy_config.get("service_policies") or []}

w = WorkspaceClient()
print(f"Deploying {len(config['model_services'])} model service(s) to {CATALOG}.{SCHEMA}")
print(f"Loaded {len(service_policies)} service policy(ies). Default evaluator: {DEFAULT_EVALUATOR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Model service helpers

# COMMAND ----------


def build_destination(destination):
    """Build one routing destination (a primary destination or a fallback).

    Args:
        destination (dict): An entry from `destinations` or `fallbacks` in model-services.yaml.

    Returns:
        dict: A destination in the format the Unity AI Gateway API expects.

    Raises:
        ValueError: If `provider` is not one of the supported values.
    """
    provider = destination["provider"]
    target = destination["destination"]

    if provider == "databricks":
        result = {
            "name": target,
            "destination_type": "DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL",
            "pay_per_token_config": {"model": f"models/{target}"},
        }
    elif provider == "provisioned_throughput":
        result = {
            "name": target,
            "destination_type": "DESTINATION_TYPE_PROVISIONED_THROUGHPUT_FOUNDATION_MODEL",
            "provisioned_throughput_config": {"model_serving_endpoint": f"serving-endpoints/{target}"},
        }
    elif provider == "external":
        result = {
            "name": f"{target}.{destination['external_model']}",
            "destination_type": "DESTINATION_TYPE_EXTERNAL_FOUNDATION_MODEL",
            "external_model_config": {
                "model_provider_service": f"model-provider-services/{target}",
                "target": {"model": destination["external_model"]},
            },
        }
    else:
        raise ValueError(f"Unknown provider '{provider}'. Use databricks, provisioned_throughput, or external.")

    if "traffic_percentage" in destination:
        result["traffic_percentage"] = destination["traffic_percentage"]
    return result


def build_routing(service):
    """Build the destinations and fallbacks for a model service.

    Args:
        service (dict): One entry from `model_services` in model-services.yaml.

    Returns:
        dict: The routing configuration in the format the Unity AI Gateway API expects.

    Raises:
        ValueError: If there are several destinations and their traffic percentages don't add up to 100.
    """
    destinations = service["destinations"]
    if len(destinations) > 1 and sum(d.get("traffic_percentage", 0) for d in destinations) != 100:
        raise ValueError(f"traffic_percentage values for '{service['name']}' must add up to 100.")

    routing = {"destinations": [build_destination(d) for d in destinations]}
    if service.get("fallbacks"):
        routing["fallback"] = {"destinations": [build_destination(d) for d in service["fallbacks"]]}
    return routing


def build_rate_limits(limits):
    """Build a per-user rate limit from the config file.

    Args:
        limits (dict): Contains `requests_per_minute` and/or `tokens_per_minute`.

    Returns:
        list[dict]: Rate limits in the format the Unity AI Gateway API expects.
    """
    rate_limit = {
        "key": "RATE_LIMIT_KEY_USER_DEFAULT",
        "renewal_period": "RATE_LIMIT_RENEWAL_PERIOD_MINUTE",
    }
    if "requests_per_minute" in limits:
        rate_limit["requests"] = limits["requests_per_minute"]
    if "tokens_per_minute" in limits:
        rate_limit["tokens"] = limits["tokens_per_minute"]
    return [rate_limit]


def build_model_service(service):
    """Build the create or update request body for one model service.

    Args:
        service (dict): One entry from `model_services` in model-services.yaml.

    Returns:
        dict: The model service request body, without service policies.
    """
    service_config = {"routing": build_routing(service)}

    if service.get("enable_inference_table", True):
        # Logs requests and responses to <catalog>.<schema>.<service name>_payload.
        service_config["inference_table"] = {
            "parent": f"schemas/{CATALOG}.{SCHEMA}",
            "table_name_prefix": service["name"],
        }

    if service.get("enable_rate_limits", True):
        limits = service.get("rate_limits", config["rate_limits"])
        service_config["rate_limits"] = build_rate_limits(limits)

    return {"comment": service.get("description", ""), "config": service_config}


def get_model_service(full_name):
    """Look up a model service.

    Args:
        full_name (str): The service's three-part name, `catalog.schema.service`.

    Returns:
        dict | None: The model service, or None if it doesn't exist.
    """
    try:
        return w.api_client.do("GET", f"{MODEL_SERVICES_API}/{full_name}")
    except NotFound:
        return None


# COMMAND ----------

# MAGIC %md
# MAGIC ## Service policy helpers

# COMMAND ----------

BUILTIN_TYPES = ["block_unsafe_content", "block_jailbreak", "block_hallucination", "detect_sensitive_data"]
DEFAULT_PHASES = {"block_jailbreak": ["input"], "block_hallucination": ["output"], "llm_judge": ["input"]}
PHASES = {"input": "pre_call", "output": "post_call"}
# detect_sensitive_data actions: the config uses the UI's names, and the API calls redact "transform".
SENSITIVE_DATA_ACTIONS = {"block": "block", "redact": "transform"}


def build_policy_options(policy):
    """Build the behavior options for a built-in or LLM judge policy.

    Args:
        policy (dict): One entry from `service_policies` in service-policies.yaml.

    Returns:
        dict: Options such as phases, mode, evaluator, and action.

    Raises:
        ValueError: If a phase or sensitive data action is invalid, or an LLM judge policy has no evaluator.
    """
    policy_type = policy["type"]
    phases = policy.get("phases", DEFAULT_PHASES.get(policy_type, ["input", "output"]))
    for phase in phases:
        if phase not in PHASES:
            raise ValueError(f"Unknown phase '{phase}' in policy '{policy['name']}'. Use input or output.")

    options = {
        "phases": ",".join(PHASES[p] for p in phases),
        "dry_run": "true" if policy.get("mode") == "log" else "false",
    }

    if policy_type == "detect_sensitive_data":
        action = policy.get("action", "block")
        if action not in SENSITIVE_DATA_ACTIONS:
            raise ValueError(f"Unknown action '{action}' in policy '{policy['name']}'. Use block or redact.")
        options["action"] = SENSITIVE_DATA_ACTIONS[action]
        options["categories"] = ",".join(c if c.startswith("class.") else f"class.{c}" for c in policy["categories"])
        return options

    # Every other type uses an LLM evaluator.
    options["max_turns"] = str(policy.get("max_turns", 10))
    evaluator = policy.get("evaluator", DEFAULT_EVALUATOR)
    if evaluator:
        options["model_service"] = f"model-services/{evaluator}"

    if policy_type == "llm_judge":
        if not evaluator:
            raise ValueError(f"LLM judge policy '{policy['name']}' needs an evaluator.")
        options["action"] = "block"
        options["instruction"] = policy["instruction"]
    return options


def build_service_policy(name, position):
    """Build one service policy from its definition in service-policies.yaml.

    Args:
        name (str): The policy name, as listed in model-services.yaml.
        position (int): The policy's position in the service's list. Used as its rank unless the policy sets one.

    Returns:
        dict: The service policy in the format the Unity AI Gateway API expects.

    Raises:
        ValueError: If the policy isn't defined or has an unknown type.
    """
    if name not in service_policies:
        raise ValueError(f"Service policy '{name}' is not defined in {SERVICE_POLICIES_PATH}.")
    policy = service_policies[name]
    policy_type = policy["type"]
    rank = policy.get("rank", position)

    if policy_type == "sql":
        return {
            "name": name,
            "policy_type": "POLICY_TYPE_CUSTOM",
            "handler": f"functions/{CATALOG}.{SCHEMA}.{name}",
            "rank": rank,
        }
    if policy_type == "llm_judge":
        handler = "system.ai.invoke_llm_judge"
    elif policy_type in BUILTIN_TYPES:
        handler = f"system.ai.{policy_type}"
    else:
        raise ValueError(
            f"Service policy '{name}' has unknown type '{policy_type}'. "
            f"Use one of: {', '.join(BUILTIN_TYPES)}, llm_judge, sql."
        )

    return {
        "name": name,
        "policy_type": "POLICY_TYPE_BUILTIN",
        "handler": handler,
        "rank": rank,
        "options": build_policy_options(policy),
    }


def create_sql_policy_functions():
    """Create or replace a Unity Catalog function for each `sql` service policy."""
    for policy in service_policies.values():
        if policy["type"] != "sql":
            continue
        function_name = f"{CATALOG}.{SCHEMA}.{policy['name']}"
        comment = policy.get("description", "").replace("'", "\\'")
        spark.sql(f"""
            CREATE OR REPLACE FUNCTION {function_name}(event VARIANT)
            RETURNS VARIANT
            LANGUAGE SQL
            COMMENT '{comment}'
            RETURN {policy['sql']}
        """)
        print(f"Created policy function {function_name}")


def set_service_policies(full_name, policies):
    """Replace all service policies on a model service.

    Args:
        full_name (str): The service's three-part name, `catalog.schema.service`.
        policies (list[dict]): Service policies built by `build_service_policy`. An empty list removes them all.
    """
    w.api_client.do(
        "PATCH",
        f"{MODEL_SERVICES_API}/{full_name}",
        query={"update_mask": "config.service_policies"},
        body={"config": {"service_policies": policies}},
    )
    print(f"  Service policies: {', '.join(p['name'] for p in policies) or 'none'}")


# COMMAND ----------

# MAGIC %md
# MAGIC ## Deploy

# COMMAND ----------


def deploy_model_service(service):
    """Create or update one model service and set its service policies.

    Args:
        service (dict): One entry from `model_services` in model-services.yaml.
    """
    full_name = f"{CATALOG}.{SCHEMA}.{service['name']}"

    # Build everything first, so a config mistake stops before anything changes.
    body = build_model_service(service)
    policies = [build_service_policy(name, i) for i, name in enumerate(service.get("service_policies", []), start=1)]

    existing = get_model_service(full_name)
    if existing:
        w.api_client.do("PATCH", f"{MODEL_SERVICES_API}/{full_name}", query={"update_mask": UPDATE_MASK}, body=body)
        print("  Updated")
    else:
        w.api_client.do(
            "POST",
            MODEL_SERVICES_API,
            query={"parent": f"schemas/{CATALOG}.{SCHEMA}", "model_service_id": service["name"]},
            body=body,
        )
        print("  Created")

    # Only call the service policy API when there are policies to set or old ones to remove.
    old_policies = (existing or {}).get("config", {}).get("service_policies")
    if policies or old_policies:
        set_service_policies(full_name, policies)


create_sql_policy_functions()

failed = []
for service in config["model_services"]:
    print(f"\n{CATALOG}.{SCHEMA}.{service['name']}")
    try:
        deploy_model_service(service)
    except Exception as e:
        print(f"  FAILED: {e}")
        failed.append(service["name"])

if failed:
    raise RuntimeError(f"{len(failed)} service(s) failed: {', '.join(failed)}")
print("\nDone. All model services deployed.")
