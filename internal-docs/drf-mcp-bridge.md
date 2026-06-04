# DRF ViewSet → MCP/Falcon Bridge

## Problem

Two parallel systems maintaining the same logic:

1. **Django REST API** — DRF ViewSets with serializers (source of truth)
2. **MCP/Falcon tools** — separate hand-written tools that reimplement the same logic

## Solution

A `@expose_to_mcp` class decorator on ViewSets that auto-generates MCP tools from DRF serializers. No Pydantic models needed.

## Architecture

```
@expose_to_mcp(category="tracing", tools={...})
class ProjectView(ModelViewSet):
    serializer_class = ProjectSerializer  ← schema auto-extracted
                                            │
    ┌───────────────────────────────────────┘
    ▼
DRF Serializer fields
    │
    ├── CharField(help_text="...") → {"type": "string", "description": "..."}
    ├── IntegerField(min_value=0)  → {"type": "integer", "minimum": 0}
    ├── ChoiceField(choices=[...]) → {"type": "string", "description": "Options: ..."}
    └── read_only / org / workspace → skipped
                                            │
    ┌───────────────────────────────────────┘
    ▼
Auto-registered in ai_tools.registry
    │
    ├── MCP server (mcp_app.py) ← picks up automatically
    └── Falcon AI (agent.py)    ← picks up automatically
```

## Usage

### 1. Add help_text to serializer fields

```python
class ProjectSerializer(serializers.ModelSerializer):
    name = serializers.CharField(
        help_text="Human-readable project name. Must be unique per organization and type.",
    )
    trace_type = serializers.CharField(
        help_text="Project type: 'experiment' (for eval runs) or 'observe' (for production tracing).",
    )
```

The `help_text` becomes the MCP tool parameter description shown to the LLM.

### 2. Decorate the ViewSet

```python
from ai_tools.drf_bridge import expose_to_mcp

@expose_to_mcp(
    category="tracing",
    tools={
        # Standard CRUD — schema from serializer_class
        "retrieve": {"name": "tracing_get_project"},
        "create": {
            "name": "tracing_create_project",
            "include_fields": ["name", "model_type", "trace_type", "source"],
        },

        # List with explicit query params (no serializer for GET params)
        "list": {
            "name": "tracing_list_projects",
            "query_params": {
                "name": {"type": str, "description": "Filter by name"},
                "page_number": {"type": int, "default": 0, "description": "Page number"},
                "page_size": {"type": int, "default": 20, "description": "Items per page"},
            },
        },

        # Custom @action with its own serializer
        "update_project_name": {
            "name": "tracing_update_project_name",
            "serializer": "ProjectNameUpdateSerializer",
            "method": "POST",
        },
    },
)
class ProjectView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    serializer_class = ProjectSerializer
```

### 3. Import the view in tools/**init**.py

```python
import tracer.views.project  # noqa: F401  — registers tracing_* bridge tools
```

That's it. No Pydantic model, no separate tool file.

## Tool Config Options

| Key              | Description                                                  | Default                                   |
| ---------------- | ------------------------------------------------------------ | ----------------------------------------- |
| `name`           | Tool name (must be unique). Use `{category}_{verb}_{entity}` | `{category}_{action}`                     |
| `description`    | Tool description for LLM                                     | Auto-generated from action + entity       |
| `method`         | HTTP method                                                  | Auto from action type                     |
| `detail`         | Whether action needs a PK                                    | Auto (`True` for retrieve/update/destroy) |
| `pk_field`       | Field name holding the PK                                    | `"id"` for detail actions                 |
| `serializer`     | Override serializer class name                               | `serializer_class`                        |
| `include_fields` | Only include these serializer fields                         | All writable fields                       |
| `exclude_fields` | Skip these serializer fields                                 | org/workspace/timestamps                  |
| `query_params`   | Explicit param definitions for GET actions                   | —                                         |

## Schema Source Per Action Type

| Action                                                    | Schema Source             | Auto?    |
| --------------------------------------------------------- | ------------------------- | -------- |
| `create` / `update`                                       | `serializer_class` fields | Yes      |
| `retrieve` / `destroy`                                    | Just `pk` (auto ID field) | Yes      |
| `@action` with `@validated_request(request_serializer=X)` | Introspect `X`            | Yes      |
| `@action` with `serializer` config key                    | Named serializer class    | Yes      |
| `list` with query params                                  | `query_params` in config  | Explicit |
| `@action` reading `request.data` directly                 | `query_params` in config  | Explicit |

## Naming Convention

Pattern: **`{category}_{verb}_{entity}`** (snake_case)

This is designed around Falcon's scoring in `modes.py`:

- **+5 points** per word match in tool name vs user message
- Category prefix (`tracing_`) gives a free boost in mode-matched filtering
- Stays under 52 chars (MCP client limits)

Examples:

- `tracing_list_projects` — matches "list my tracing projects" (3 words = +15)
- `tracing_get_project` — matches "get project details" (+10)
- `datasets_create_dataset` — matches "create a new dataset" (+10)

## Serializer Best Practices for MCP

Adding `help_text` to DRF serializer fields directly improves MCP tool quality:

```python
# Before (LLM sees: "Name" with no context)
name = serializers.CharField()

# After (LLM sees: "Human-readable project name. Must be unique per organization and type.")
name = serializers.CharField(
    help_text="Human-readable project name. Must be unique per organization and type.",
)
```

Key tips:

- Include value constraints in help_text: "Options: 'experiment' or 'observe'"
- Mention uniqueness: "Must be unique within the organization"
- Use ChoiceField with explicit choices — they auto-appear in description
- Add min/max to numeric fields — auto-included in JSON schema

## How It Integrates

### MCP Server

`mcp_app.py` iterates `ai_registry.list_all()` at startup. Bridge tools appear alongside hand-written tools with no changes needed.

### Falcon AI

`agent.py` looks up tools via `tool_registry.get(tool_name)`. Bridge tools execute through the same `BaseTool.run()` → `execute()` path. Falcon's mode detection and tool filtering work on the `category` and `name` fields — both are set by the decorator.

### No Changes Required To

- `mcp_app.py` — reads registry
- `agent.py` — reads registry
- `modes.py` — filters by category/name
- `consumers.py` — creates ToolContext

## Tradeoffs vs Hand-Written Tools

| Aspect            | Hand-written                                        | Bridge                          |
| ----------------- | --------------------------------------------------- | ------------------------------- |
| Output quality    | Optimized markdown, dashboard links, relative dates | Raw REST response formatted     |
| Name resolution   | "my-dataset" → UUID                                 | UUID only                       |
| Development speed | Hours per tool                                      | Minutes (just decorator config) |
| Maintenance       | Must update when API changes                        | Zero — uses serializer directly |
| Error messages    | LLM-friendly with schema hints                      | Standard DRF errors             |

## Files

| File                                | Purpose                                                                      |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| `ai_tools/drf_bridge.py`            | Bridge framework: `expose_to_mcp`, `DRFBridgeTool`, serializer introspection |
| `tracer/views/project.py`           | First decorated ViewSet (4 bridge tools)                                     |
| `tracer/serializers/project.py`     | Enhanced with help_text for MCP                                              |
| `ai_tools/tools/__init__.py`        | Imports view to trigger registration                                         |
| `ai_tools/tests/test_drf_bridge.py` | 37 unit tests                                                                |

## Testing

```bash
# Run all bridge tests (37 tests)
cd futureagi && python -m pytest ai_tools/tests/test_drf_bridge.py -v

# Verify tools are registered with Django
DJANGO_SETTINGS_MODULE=tfc.settings.settings python -c "
import django; django.setup()
from ai_tools.registry import registry
for t in registry.list_all():
    if t.name.startswith('tracing_'):
        print(f'{t.name}: {list(t.input_schema.get(\"properties\", {}).keys())}')
"
```

## Migrating an Existing ViewSet

1. Add `help_text` to serializer fields (improves ALL consumers, not just MCP)
2. Add `@expose_to_mcp` decorator to the ViewSet class
3. Add the view import to `ai_tools/tools/__init__.py`
4. Run tests: `pytest ai_tools/tests/test_drf_bridge.py`

No hand-written tool file, no Pydantic model, no changes to MCP or Falcon code.

---

# Migration Status & Verification (2026-05-28)

## Verified working count — the number that matters

"Registered" ≠ "works." A bridge tool can register, appear in Falcon, and
dispatch — yet error at call time (wrong input schema, wrong pk kwarg).
`ai_tools/tests/verify_bridges.py` runs every list/get bridge tool against
the **live DB** (read-only) and reports the true count:

```bash
docker exec ws1-backend python -m ai_tools.tests.verify_bridges
```

Latest sweep: **244 bridge tools registered; of the testable ones, 85 working,
3 failing, 16 untestable (empty tables in this workspace — bridge dispatches
fine, no row to fetch).** The 3 failures are ws1 DB schema drift
(`agentcc_prompt_template` table and `model_hub_score.value_history` column
not migrated in this workspace) plus one tracer span-lookup edge — NOT bridge
bugs; they work against a migrated DB.

## Bridge bugs found & fixed via the live sweep

The first sweep found only 20/94 working. Root causes (all fixed):

1. **List actions inherited required create-fields.** A `list` action without
   explicit `query_params` was given the model serializer as its input schema,
   so `list_datasets({})` failed validation demanding `name`. Fix: list/GET
   collection actions take only optional `search`/`page`/`page_size`.
2. **Detail actions only set `kwargs["pk"]`.** DRF `get_object()` reads
   `self.kwargs[lookup_url_kwarg or lookup_field]`. ViewSets with
   `lookup_field="id"` raised "expected kwarg 'pk'". Fix: set the id under
   `pk` AND the viewset's lookup names.
3. **Acronym entity names.** `APIKey → a_p_i_key`. Fix: collapse capital runs.

Lesson: **always run verify_bridges.py after adding bridges.** Unit tests mock
the ViewSet and stay green even when the real call is broken.

## Remaining hand-written tools (126) — classified backlog

A per-tool classification (reading each tool + its candidate views) bucketed
the remaining hand-written tools:

| Bucket                  | Count | Meaning                                                                                  | Action                                                               |
| ----------------------- | ----: | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| KEEP                    |    16 | Custom keepers (context, web, docs, usage, viz, falcon memory)                           | Stay hand-written — no REST analogue / external services             |
| BRIDGE_APIVIEW          |    52 | Maps to a distinct APIView get/post; id often a view-specific URL kwarg                  | Bridgeable; each detail tool needs `pk_kwarg` config + a verify pass |
| CUSTOM_ACTION / @action |   ~39 | Maps to an `@action` method (commit, pause, apply, trial\_\*, graph) — NOT standard CRUD | Bridgeable via explicit action+serializer config + verify            |
| NEW_ENDPOINT_NEEDED     |    19 | No Django view exposes the operation (tool does direct ORM)                              | Needs a new APIView+serializer built first                           |

The 91 bridgeable (52 + 39) are real per-tool work: each needs its exact
target action, id-kwarg, and serializer wired, then verified against the live
DB. They are NOT a safe batch operation — blind registration produces
call-time-broken stubs (see the 20/94 lesson above).

## The 16 custom keepers (final)

- **context/**: `whoami`, `search`, `read_schema`, `read_taxonomy`
- **web/**: `brave_search`, `ground_truth_search`, `kb_search`, `trace_explorer`
- **docs/**: `ask_docs`, `search_docs`, `get_page`
- **usage/**: `get_cost_breakdown`
- **visualization/**: `render_widget`
- **falcon EE memory**: `save_memory`, `list_memories`, `delete_memory`
