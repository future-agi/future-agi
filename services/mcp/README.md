# FutureAGI direct-tools MCP

This service exposes selected FutureAGI API operations directly as MCP tools.
There are no `search_capabilities`, `describe_operation`, or
`execute_operation` meta-tools and no handwritten API wrapper per tool.

```text
definitions/*.yaml (small reviewed allowlist)
           +
api_contracts/openapi/swagger.json (paths and schemas)
           |
           v
scripts/generate.ts
           |
           +--> generated tool metadata
           +--> generated strict Zod schemas
           |
           v
MCP tools/list: list_datasets, list_traces, ...
           |
           v
generic API executor --> existing Django API view
```

The YAML definition decides which tools are exposed and supplies their stable
model-facing names and descriptions. Everything repetitive—method, path,
parameters, validation, authentication forwarding, and request execution—is
generated or generic.

## Adding a tool

1. Find the endpoint's unique `operationId` in the committed OpenAPI contract.
2. Add one entry to the appropriate `definitions/*.yaml` file.
3. Run `yarn mcp:generate` and review the generated contract.
4. Run `yarn mcp:check`.

Do not create a Python `ai_tools` wrapper for an endpoint-backed capability.
Keep handwritten tools only for genuinely multi-step or agentic behavior that
cannot be represented by one API operation.

The initial four read-only tools prove the architecture. The remaining tool
set is curated feature by feature so only useful, clearly described API
operations enter the catalog.

Clients can narrow that catalog per connection without creating another
server. `features` selects one or more YAML groups, `tools` selects exact tool
names, and the two filters are combined as a union. `readonly=true` is then
applied as a safety restriction:

```text
/mcp?features=datasets,observability
/mcp?tools=list_datasets,list_traces
/mcp?features=datasets&tools=list_traces&readonly=true
```

Unknown filters fail closed with HTTP 400 instead of accidentally exposing a
broader catalog.

## Authentication

The gateway accepts the existing `X-Api-Key` plus `X-Secret-Key` pair, or a
bearer credential accepted by the API, and forwards it to Django. Django
remains the authorization, organization, workspace, and business-logic
boundary.

## Commands

```bash
yarn --cwd services/mcp install --frozen-lockfile
yarn mcp:generate
yarn mcp:check
MCP_API_BASE_URL=http://localhost:8000 yarn --cwd services/mcp dev
```

Connect an MCP client to `http://localhost:3001/mcp`.
