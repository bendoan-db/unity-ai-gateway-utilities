"""Dry-run a deploy-uaig notebook: run its code with a fake Databricks client and print the calls it would make.

Usage:
    python dry_run.py <path/to/notebook.py> [--existing catalog.schema.service ...]

Needs databricks-sdk and pyyaml installed. Makes no API calls and changes nothing.
"""

import argparse
import json
import os
from unittest import mock

from databricks.sdk.errors import NotFound


def code_cells(path):
    """Return the Python cells of a Databricks source-format notebook.

    Args:
        path (str): Path to the notebook `.py` file.

    Returns:
        list[str]: Cells that contain Python code. Markdown and magic cells are skipped.
    """
    with open(path) as f:
        cells = f.read().split("# COMMAND ----------")
    return [
        c for c in cells
        if any(line.strip() and not line.startswith(("# MAGIC", "# Databricks")) for line in c.splitlines())
    ]


def fake_client(existing):
    """Build a fake WorkspaceClient that prints write calls instead of sending them.

    Args:
        existing (list[str]): Three-part names of model services to treat as already existing.

    Returns:
        MagicMock: A stand-in for `databricks.sdk.WorkspaceClient()`.
    """
    client = mock.MagicMock()

    def api_call(method, path, query=None, body=None, **kwargs):
        """Answer GETs from `existing`, and print every other request."""
        if method == "GET":
            if path.rsplit("/", 1)[-1] in existing:
                return {"config": {}}
            raise NotFound("not found")
        print(f"\n[API] {method} {path} {json.dumps(query or {})}")
        print(json.dumps(body, indent=2))
        return {}

    def grant(securable_type, full_name, changes=None, **kwargs):
        """Print each privilege change."""
        for change in changes or []:
            print(f"[GRANT] {securable_type} {full_name}: {change.principal} += {[p.value for p in change.add]}")

    client.api_client.do.side_effect = api_call
    client.grants.update.side_effect = grant
    return client


def main():
    """Run the notebook given on the command line against the fake client."""
    parser = argparse.ArgumentParser(description="Dry-run a deploy-uaig notebook.")
    parser.add_argument("notebook", help="Path to the notebook .py file.")
    parser.add_argument("--existing", nargs="*", default=[], help="Model services to treat as already existing.")
    args = parser.parse_args()

    notebook = os.path.abspath(args.notebook)
    spark = mock.MagicMock()
    spark.sql.side_effect = lambda statement: print(f"\n[SQL]{statement}")

    # Notebooks read their YAML files from their own folder.
    os.chdir(os.path.dirname(notebook))
    with mock.patch("databricks.sdk.WorkspaceClient", return_value=fake_client(args.existing)):
        namespace = {"spark": spark}
        for cell in code_cells(notebook):
            exec(cell, namespace)


if __name__ == "__main__":
    main()
