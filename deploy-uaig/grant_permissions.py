# Databricks notebook source
# MAGIC %md
# MAGIC # Grant access to Unity AI Gateway model services
# MAGIC
# MAGIC Grants the groups and users in `permissions.yaml` access to the model services listed for them.
# MAGIC
# MAGIC **How to use**
# MAGIC 1. Edit `permissions.yaml` (in the same folder as this notebook).
# MAGIC 2. Run all cells.
# MAGIC
# MAGIC **What it does**
# MAGIC - Grants each group or user the listed privileges (default `EXECUTE`) on each of their model services.
# MAGIC - Grants `USE CATALOG` and `USE SCHEMA` on those services' catalogs and schemas, which they need to reach the services.
# MAGIC - Only adds access, never removes it. Changes can take a few minutes to take effect.
# MAGIC
# MAGIC **You need**
# MAGIC - `MANAGE` (or ownership) on each model service, and on its catalog and schema.

# COMMAND ----------

import yaml
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import PermissionsChange, Privilege

PERMISSIONS_PATH = "permissions.yaml"

with open(PERMISSIONS_PATH) as f:
    config = yaml.safe_load(f) or {}

principals = (config.get("groups") or []) + (config.get("users") or [])

w = WorkspaceClient()
print(f"Granting access for {len(principals)} group(s) and user(s)")

# COMMAND ----------


def grant(securable_type, full_name, principal, privileges):
    """Add privileges for one group or user on one Unity Catalog object.

    Args:
        securable_type (str): `catalog`, `schema`, or `model_service`.
        full_name (str): The object's full name, such as `my_catalog.my_schema`.
        principal (str): Group name, user email, or service principal application ID.
        privileges (list[Privilege]): Privileges to add.
    """
    w.grants.update(securable_type, full_name, changes=[PermissionsChange(principal=principal, add=privileges)])


def grant_model_service_access(entry):
    """Grant one group or user access to the model services listed for it.

    Args:
        entry (dict): One entry from `groups` or `users` in permissions.yaml.

    Raises:
        ValueError: If a model service name isn't a three-part name.
    """
    principal = entry["name"]
    services = entry.get("model_services") or []

    # Check the entry before granting anything.
    for service in services:
        if len(service.split(".")) != 3:
            raise ValueError(f"'{service}' is not a three-part name (catalog.schema.service).")
    privileges = [Privilege(p.upper().replace(" ", "_")) for p in entry.get("privileges", ["EXECUTE"])]

    # Users need access to a service's catalog and schema to reach the service.
    for catalog in sorted({s.split(".")[0] for s in services}):
        grant("catalog", catalog, principal, [Privilege.USE_CATALOG])
    for schema in sorted({s.rsplit(".", 1)[0] for s in services}):
        grant("schema", schema, principal, [Privilege.USE_SCHEMA])

    for service in services:
        grant("model_service", service, principal, privileges)
        print(f"  Granted {', '.join(p.value for p in privileges)} on {service}")


# COMMAND ----------

failed = []
for entry in principals:
    print(f"\n{entry['name']}")
    try:
        grant_model_service_access(entry)
    except Exception as e:
        print(f"  FAILED: {e}")
        failed.append(entry["name"])

if failed:
    raise RuntimeError(f"{len(failed)} group(s) or user(s) failed: {', '.join(failed)}")
print("\nDone. All access granted.")
