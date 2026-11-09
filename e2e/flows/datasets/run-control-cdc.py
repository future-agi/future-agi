"""Read-only CDC parity check, run inside the managed stack's backend container."""

import argparse
import json
import os
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg


TABLES = (
    "model_hub_dataset",
    "model_hub_column",
    "model_hub_row",
    "model_hub_cell",
    "model_hub_score",
)


def clickhouse(sql, parameters=None):
    query = urlencode({
        "database": os.environ.get("CH_DATABASE", "default"),
        **{f"param_{key}": value for key, value in (parameters or {}).items()},
    })
    request = Request(
        f"http://clickhouse:8123/?{query}",
        data=(sql + " FORMAT JSONEachRow").encode(),
        headers={
            "X-ClickHouse-User": "default",
            "X-ClickHouse-Key": os.environ.get("CH_PASSWORD", "e2e-clickhouse-password"),
        },
    )
    with urlopen(request, timeout=20) as response:
        return [json.loads(line) for line in response.read().splitlines()]


def verify(since, minimum):
    # The recipe only runs this script in the disposable stack network. No configurable host.
    with psycopg.connect(
        host="postgres", port=5432, dbname="futureagi", user="futureagi",
        password="futureagi", autocommit=True,
    ) as connection, connection.cursor() as cursor:
        parity = {}
        for table in TABLES:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=%s AND column_name IN ('organization_id','workspace_id','tracer_project_id') ORDER BY column_name",
                [table],
            )
            expected = [row[0] for row in cursor.fetchall()]
            actual = [row["name"] for row in clickhouse(
                "SELECT name FROM system.columns WHERE database=currentDatabase() "
                "AND table={table:String} AND name IN ('organization_id','workspace_id','tracer_project_id') ORDER BY name",
                {"table": table},
            )]
            assert actual == expected, (table, expected, actual)
            parity[table] = actual

        cursor.execute(
            "SELECT e.id::text,e.snapshot_dataset_id::text,d.organization_id::text,d.workspace_id::text "
            "FROM model_hub_experimentstable e JOIN model_hub_dataset d ON d.id=e.snapshot_dataset_id "
            "WHERE e.name LIKE 'e2e-control-%%' AND e.created_at >= %s ORDER BY e.id", [since],
        )
        experiments = cursor.fetchall()
        assert len(experiments) >= minimum, (len(experiments), minimum)
        assert len({row[2] for row in experiments}) >= 2, "Need independent synthetic organizations"
        results = []
        for experiment, dataset, organization, workspace in experiments:
            cursor.execute(
                "SELECT c.id::text,c.dataset_id::text,c.column_id::text,c.row_id::text,c.value,c.status,"
                "to_char(c.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US') "
                "FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id "
                "JOIN model_hub_experimentdatasettable_columns link ON link.column_id=col.id "
                "JOIN model_hub_experimentdatasettable run ON run.id=link.experimentdatasettable_id "
                "WHERE run.experiment_id=%s AND NOT c.deleted AND NOT col.deleted ORDER BY c.id", [experiment],
            )
            expected_cells = sorted(list(row) for row in cursor.fetchall())
            assert len(expected_cells) == 144, (experiment, "two runs x 24 rows x output/eval/reason", len(expected_cells))
            assert all(row[5] == "pass" for row in expected_cells), (experiment, "non-passing cells")
            column_ids = sorted({row[2] for row in expected_cells})
            query = (
                "SELECT toString(c.id) AS id,toString(c.dataset_id) AS dataset_id,"
                "toString(c.column_id) AS column_id,toString(c.row_id) AS row_id,c.value,c.status,"
                "toString(c.updated_at) AS updated_at FROM model_hub_cell AS c FINAL "
                "INNER JOIN model_hub_dataset AS d FINAL ON d.id=c.dataset_id "
                "WHERE c.dataset_id={dataset:UUID} AND d.organization_id={organization:UUID} "
                "AND d.workspace_id={workspace:UUID} AND c.column_id IN {columns:Array(UUID)} "
                "AND NOT c.deleted AND NOT c._peerdb_is_deleted AND NOT d.deleted "
                "AND NOT d._peerdb_is_deleted ORDER BY c.id"
            )
            params = {"dataset": dataset, "organization": organization, "workspace": workspace,
                      "columns": "[" + ",".join("'" + value + "'" for value in column_ids) + "]"}
            deadline = time.monotonic() + 180  # POLL.CDC_VISIBLE: source writes -> PeerDB mirror.
            while True:
                actual_cells = sorted([row[key] for key in (
                    "id", "dataset_id", "column_id", "row_id", "value", "status", "updated_at",
                )] for row in clickhouse(query, params))
                if actual_cells == expected_cells:
                    break
                assert time.monotonic() < deadline, (experiment, "CDC mismatch", expected_cells, actual_cells)
                time.sleep(1)
            other_org = next(row[2] for row in experiments if row[2] != organization)
            assert clickhouse(query, {**params, "organization": other_org}) == []
            results.append({"experiment": experiment, "dataset": dataset, "organization": organization,
                            "workspace": workspace, "cells": len(expected_cells), "columns": len(column_ids)})
        print(json.dumps({"tenantColumns": parity, "experiments": results,
                          "checks": "exact cell identities, values, statuses, timestamps and dataset tenant joins"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, type=datetime.fromisoformat)
    parser.add_argument("--minimum", type=int, default=20)
    arguments = parser.parse_args()
    verify(arguments.since, arguments.minimum)
