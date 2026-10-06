#!/usr/bin/env python3
"""Exercise a running MCP endpoint using a private JSON credentials file.

Run with futureagi/.venv/bin/python scripts/smoke-mcp.py --credentials /path/to/keys.json.
The file needs api_key and secret_key; --write creates named smoke-test resources.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(args):
    credentials = json.loads(Path(args.credentials).read_text())
    headers = {
        "X-Api-Key": credentials["api_key"],
        "X-Secret-Key": credentials["secret_key"],
    }
    async with (
        httpx.AsyncClient(headers=headers, timeout=60) as http,
        streamable_http_client(args.url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as client,
    ):
        await client.initialize()
        listing = await client.list_tools()
        tools = {tool.name for tool in listing.tools}
        print(f"Connected to {args.url}: {len(tools)} tools", flush=True)

        async def call(name, arguments=None, *, expect_error=False):
            started = time.perf_counter()
            result = await client.call_tool(name, arguments or {})
            elapsed = int((time.perf_counter() - started) * 1000)
            if bool(result.isError) != expect_error:
                raise AssertionError(f"{name}: {result.model_dump_json()[:1000]}")
            print(f"PASS {name} ({elapsed} ms)", flush=True)
            return result.structuredContent

        identity = await call("whoami")
        if credentials.get("workspace_id"):
            assert identity["default_workspace_id"] == credentials["workspace_id"]
        await call("list_workspaces")
        for name, arguments in [
            ("list_datasets", {"page_size": 2}),
            ("list_prompt_templates", {"limit": 2}),
            ("list_eval_groups", {"page_number": 0, "page_size": 2}),
            ("list_projects", {"page_number": 0, "page_size": 2}),
            ("list_knowledge_bases", {"limit": 2}),
            ("list_test_executions", {"limit": 2, "search": "mcp-smoke"}),
            ("list_optimization_runs", {"limit": 2}),
        ]:
            if name in tools:
                await call(name, arguments)

        if args.write:
            suffix = uuid4().hex[:12]
            prompt = await call(
                "create_prompt_template",
                {
                    "name": f"mcp-smoke-{suffix}",
                    "description": None,
                },
            )
            prompt_id = prompt["id"]
            await call(
                "update_prompt_template",
                {
                    "id": prompt_id,
                    "description": "Verified over Streamable HTTP",
                    "prompt_folder": None,
                },
            )
            read = await call("get_prompt_template", {"id": prompt_id})
            assert read["description"] == "Verified over Streamable HTTP"
            await call("list_prompt_versions", {"id": prompt_id, "limit": 1})
            dataset = await call(
                "create_dataset", {"new_dataset_name": f"mcp-smoke-{suffix}"}
            )
            dataset_id = dataset["dataset_id"]
            await call(
                "create_dataset_column",
                {
                    "dataset_id": dataset_id,
                    "new_column_name": "input",
                    "column_type": "text",
                },
            )
            await call(
                "add_dataset_rows",
                {
                    "dataset_id": dataset_id,
                    "rows": [{"input": "invalid old shape"}],
                },
                expect_error=True,
            )
            await call(
                "add_dataset_rows",
                {
                    "dataset_id": dataset_id,
                    "rows": [
                        {
                            "cells": [
                                {"column_name": "input", "value": "MCP smoke value"}
                            ]
                        }
                    ],
                },
            )
            rows = await call("get_dataset", {"dataset_id": dataset_id, "page_size": 2})
            assert "MCP smoke value" in json.dumps(rows)
            print(
                json.dumps(
                    {"created_prompt_id": prompt_id, "created_dataset_id": dataset_id}
                ),
                flush=True,
            )

            base_url = args.url.rstrip("/").removesuffix("/mcp")
            group_url = base_url + "/mcp/config/tool-groups/"
            current = await http.get(group_url)
            current.raise_for_status()
            enabled = current.json()["result"]["enabled_groups"]
            try:
                disabled = await http.put(
                    group_url,
                    json={
                        "enabled_groups": [
                            group for group in enabled if group != "datasets"
                        ],
                    },
                )
                disabled.raise_for_status()
                visible = await client.list_tools()
                assert "list_datasets" not in {tool.name for tool in visible.tools}
                await call("list_datasets", expect_error=True)
            finally:
                restored = await http.put(group_url, json={"enabled_groups": enabled})
                restored.raise_for_status()
            await call("list_datasets", {"page_size": 2})
            print("PASS tool-group configuration and restoration", flush=True)

        print("MCP smoke checks passed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000/mcp")
    parser.add_argument("--credentials", required=True)
    parser.add_argument(
        "--write", action="store_true", help="Create smoke-test prompt and dataset"
    )
    asyncio.run(main(parser.parse_args()))
