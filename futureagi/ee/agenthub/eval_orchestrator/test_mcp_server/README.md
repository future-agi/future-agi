# Text Quality MCP Server (Test)

A local MCP server with text analysis tools, used to test the orchestrator's MCP integration end-to-end.

## Tools

| Tool | Description |
|------|-------------|
| `passage_summary_scorer` | Scores how well a summary captures a source passage (0-100) |
| `grammar_checker` | Checks text for grammar/spelling/style issues |
| `readability_scorer` | Flesch-Kincaid readability analysis |

## Setup

```bash
cd agentic_eval/agenthub/eval_orchestrator/test_mcp_server
npm install
node server.mjs
# Runs on port 3456
```

## Connect to Prism Gateway

1. Add the server to `agentcc-gateway/config.yaml`:

```yaml
mcp:
  servers:
    text-quality:
      url: "http://host.docker.internal:3456"
      transport: "http"
```

2. Create a `PrismOrgConfig` with MCP enabled for the test org:

```python
from prism.models.org_config import PrismOrgConfig

PrismOrgConfig.no_workspace_objects.update_or_create(
    organization_id="<ORG_ID>",
    is_active=True,
    deleted=False,
    defaults={
        "mcp": {
            "servers": {
                "text-quality": {
                    "url": "http://host.docker.internal:3456",
                    "transport": "http",
                }
            }
        }
    },
)
```

3. Restart the gateway:

```bash
docker restart agentcc-gateway
```

4. Verify tools are discovered:

```bash
curl -H "Authorization: Bearer agentcc-admin-secret" http://localhost:8090/-/mcp/tools
```

## Run MCP test cases

```bash
docker exec backend bash -c \
    "cd /app/backend && \
     AGENTCC_GATEWAY_URL=http://agentcc-gateway:8090 \
     AGENTCC_ADMIN_TOKEN=agentcc-admin-secret \
     PYTHONPATH=/app/backend \
     python agentic_eval/agenthub/eval_orchestrator/run_real_integration_tests.py --filter MCP"
```
