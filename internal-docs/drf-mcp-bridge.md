# DRF ViewSet → MCP/Falcon Bridge

The definitive internal reference for adding agent tools at FutureAGI. This document supersedes `internal-docs/drf-mcp-bridge.md` (whose counts and naming claims are stale — see corrections throughout).

Code source of truth: `futureagi/ai_tools/drf_bridge.py`. Line numbers are approximate (`~`) and drift; trust the symbol names.

---

## 1. TL;DR + The Golden Rule

**THE GOLDEN RULE: A new agent tool is a thin `@expose_to_mcp` bridge over an existing DRF ViewSet/APIView. NEVER hand-write a custom tool class for anything a DRF view already does.**

The bridge introspects the serializer and view, so the **DRF layer is the single source of truth** for the field contract, validation, and response shape. A hand-written tool forks that truth and drifts from it — preventing exactly that fork is why this system exists.

What "add a tool" looks like in practice:

```python
from ai_tools.drf_bridge import expose_to_mcp

@expose_to_mcp(category="tracing", tools={"list": {"name": "list_trace_projects"}, ...})
class ProjectView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    serializer_class = ProjectSerializer
```

…then one import line in `ai_tools/tools/__init__.py`. No Pydantic model, no `execute()`, no tool file.

Mental model: `@expose_to_mcp` is a **class decorator** that, at import time, walks a ViewSet/APIView's actions; for each one it synthesizes a `DRFBridgeTool` (a `BaseTool`), builds its Pydantic `input_model` by introspecting the action's DRF serializer, and registers a live instance into the global `ToolRegistry` singleton. At call time the tool fabricates a DRF `Request`, instantiates the view, and **calls the action method directly** (not via `dispatch()`), then unwraps and Markdown-formats the response.

The only two things you ever touch when adding a tool: the **decorator** (on the view, or programmatically in a `bridge/` module) and the **import** that triggers it. Everything downstream (registry, MCP server, Falcon loop, discovery endpoint) is generic over the registry and needs zero edits.

**Live snapshot — registry-verified 2026-06-04; re-run the counts (do not trust hardcoded numbers, §8):**

- **393 tools total** (390 in a bare shell; +3 EE memory keepers from `FalconAIConfig.ready()`)
- **252 bridge tools** (`DRFBridgeTool`)
- **141 hand-written tools** (`@register_tool`) — of which only **~16 + 2** are true keepers (§9); the rest are *backlog* still to be bridged.
- **~64 bridged view classes** · **13 domain bridge modules** (`bridge/_*.py`, excluding `__init__.py`)

Regenerate any of these from the registry rather than trusting the printed number:

```python
from ai_tools.registry import registry
registry.count()         # 393
registry.categories()    # category list
len([t for t in registry.list_all() if type(t).__name__ == "DRFBridgeTool"])   # 252
```

---

## 2. Why this exists — the two-parallel-systems problem

Before the bridge, agent tools were hand-written `execute()` classes that re-implemented logic the DRF API already had. That created **two parallel systems** describing the same operation: the REST serializer/view, and a divergent Python tool. They drifted. Three concrete failures drove the rule:

- **Stale data contracts.** A hand-written `get_call_execution` / `get_scenario` returned a hand-curated subset; the serializer already exposed `wpm`, `talk_ratio`, the scenario graph — fields the custom tool never surfaced. Fix (commit `1cbc78d15`): delete the custom tool, bridge the view, get the real shape for free.
- **Reverted custom edits.** When data was missing, the instinct was to patch the custom tool's `execute()`. Direction was explicit (`47de10cdc`): "surface via the DRF serializer/view + bridge," not custom logic. Hand-written `list_trace_scores` was replaced by the bridged `list_scores` (`ea87bf600` / `93b911b37`).
- **Composite sprawl.** 64 hand-written tools pre-joined 3+ models against no real endpoint, hiding structure. Deleting them (`b6833e4fa`) made the surface 1:1 with the API — the PostHog/Anthropic pattern — and let the LLM chain individual calls itself.

**One source of truth:** improve the serializer once (a `help_text`, a `query_serializer`) and the improvement flows verbatim into every consumer — REST clients, the MCP server, and Falcon — with zero duplication. The bridge is the mechanism that makes "fix it in the DRF layer" the path of least resistance.

---

## 3. How it works (architecture)

Pipeline, with the real symbols:

```
@expose_to_mcp(category, tools, verb_map)        # class decorator, drf_bridge.py ~940
        │  (runs at IMPORT time)
        ▼
expand tools → [(action_name, config), ...]      # sorted: list/get first, then create, then rest
        ▼
_register_bridge_tool(...)  (per action)         # ~746
        ├── _get_action_serializer(...)          # ~562  resolve the right serializer
        │      └── _extract_serializer_from_validated_request(...)  # ~535  closure walk
        ├── _serializer_to_pydantic(...)         # ~438  serializer → Pydantic input_model
        ├── _derive_tool_name / _derive_entity_name / _derive_description   # ~123 / ~158 / ~201
        └── registry.register(DRFBridgeTool(...)) # registry.py ~22
        ▼
ToolRegistry singleton (ai_tools/registry.py)    # indexed by name + category
        ▼
   ┌──────────────┴───────────────┐
   ▼                              ▼
MCP server (mcp_app.py)      Falcon AI (ee/falcon_ai/agent.py)
_register_ai_tools() →       load_tools_for_mode / search_tools /
mcp.tool(...) per tool       registry.get auto-load
```

**At call time** (`DRFBridgeTool.execute`, ~632), invoked via `BaseTool.run` (`base.py` ~136):

```
run() → _clean_params → validate(input_model) → workspace_context(ws, org, user):
   execute():
     _resolve_class(viewset)  → detect APIView
     PK routing (pop pk_field → pk / lookup_field / pk_kwarg)        # ~640
     _build_drf_request(...)  → APIRequestFactory, stamp user/ws/org # ~248
     _instantiate_view(...)   → set request/action/kwargs/headers    # ~279
     _resolve_apiview_handler(...)  → for APIViews, map action→verb handler # ~305
     action_method(request, **kwargs)   # DIRECT call, NOT dispatch  # ~684
     _unwrap_response(...)    → .data / file body / envelope unwrap   # ~318
     _format_result_for_llm(...) → Markdown table/kv block           # ~357
```

Three named introspection helpers you'll see referenced everywhere:

- **`_serializer_to_pydantic`** — instantiates the serializer, walks `serializer.fields`, maps each to a Pydantic field via `DRF_FIELD_TYPE_MAP`, and emits a dynamically-built `Auto_<SerializerName>` model. This is the schema the LLM sees. (Used only for the serializer-derived input branch — see §5; the `list`/`retrieve`/`destroy`/`query_params` branches hand-build the model and never touch the serializer.)
- **`_get_action_serializer`** — decides *which* serializer drives the schema for a given action (resolution order in §4). For APIViews it re-fetches the handler by HTTP verb (`method.lower()`) — this verb fallback, **not** `_resolve_apiview_handler`, is the serializer-discovery path (~607).
- **`_resolve_apiview_handler`** — a **call-time** helper (invoked inside `execute()`, ~305) that, for APIViews (which have `.get`/`.post`, not `list`/`create`), maps the configured action back to the real verb handler so the direct method call dispatches to the right handler. It plays no role in serializer discovery.

> ⚠️ **Dead-code gotcha:** `drf_bridge.py` ~49 declares `_pending_registrations = []` with a comment "processed at app ready." It is never appended to and never read. There is **no deferred mechanism** — registration is fully synchronous inside the decorator's per-action loop. The only "app-ready" part is the module *import* (via `AiToolsConfig.ready()`) that triggers the decorators.

---

## 4. How to add a tool — recipe + worked example

The golden rule decides everything before you write a line: **does a DRF view already do this?** If yes, bridge it; if it's close but wrong, improve the DRF layer then bridge; only a genuine no-REST-analogue is a custom keeper (§11 flowchart).

### Step 1 — Curate the serializer `help_text` (improves *every* consumer)

The bridge derives each input field's description from `field.help_text` (`_serializer_to_pydantic` ~469). With no `help_text`, the LLM sees a useless title-cased fallback (`"Name"`) — a real anti-pattern (§7.6). Edit the serializer once:

```python
class ProjectSerializer(serializers.ModelSerializer):
    """Project: a tracing/experiment container scoped to an org."""
    name = serializers.CharField(
        help_text="Human-readable project name. Must be unique per organization and type.",
    )
    trace_type = serializers.ChoiceField(
        choices=[("experiment", "experiment"), ("observe", "observe")],
        help_text="Project type: 'experiment' (offline eval runs) or 'observe' (live tracing).",
    )
```

Constraints flow straight into the schema: `min_value`/`max_value` → `ge`/`le`; `max_length` → `max_length`; a **static** `ChoiceField` → a Pydantic `Literal` enum. The serializer's `__doc__` becomes the tool's description suffix (`_derive_description`).

### Step 2 — Apply `@expose_to_mcp` with a `tools` mapping

`expose_to_mcp(category, tools=None, verb_map=None)`. The `tools` arg takes three forms:

- `None` → auto-expose `STANDARD_CRUD = ("list", "retrieve", "create", "update", "destroy")`.
- a **list** of action names → cherry-pick (`tools=["list", "retrieve"]`).
- a **dict** `action → config` → full control (what real code uses).

### Step 3 — Wire the import so the decorator fires

Registration is a side effect of importing the module. **The default in practice is the separate-module form:** of ~64 bridged classes, only **3** use a literal `@expose_to_mcp` on the view class; ~61 are wired programmatically as `expose_to_mcp(...)(View)` inside an `ai_tools/tools/bridge/_*.py` module. Prefer that form; put `@` directly on the view only when the view file is lint-clean and you own it.

- **Functional / separate-module form (default):** add the call in a `bridge/_xxx.py` module, then add that module to the `from ai_tools.tools.bridge import (...)` block in `ai_tools/tools/__init__.py`.
- **Colocated `@`-on-view form:** import that view module in `ai_tools/tools/__init__.py`:

```python
import tracer.views.project  # noqa: F401  — registers tracing_* bridge tools
```

> ⚠️ **The single most common mistake:** forgetting this import → the tool **silently never registers**. There is no error (see §7/§8 silent-failure note).

### Step 4 — Verify (mandatory; §8).

### Complete worked example — the real `ProjectView`

One decorator demonstrating list-with-`query_params`, id-only retrieve, create-with-`include_fields`, and a custom `@action` with a named serializer + explicit method (`tracer/views/project.py`):

```python
from ai_tools.drf_bridge import expose_to_mcp

@expose_to_mcp(
    category="tracing",
    tools={
        "list": {
            "name": "list_trace_projects",
            "query_params": {
                "name": {"type": str, "required": False,
                         "description": "Filter projects by name (case-insensitive substring)."},
                "project_type": {"type": str, "required": False,
                                 "description": "One of 'experiment' or 'observe'. Omit for both."},
                "page_number": {"type": int, "default": 0, "required": False,
                                "description": "Page number, 0-indexed."},
                "page_size": {"type": int, "default": 20, "required": False,
                              "description": "Items per page. 1-100. Default 20."},
            },
        },
        "retrieve": {"name": "get_trace_project"},
        "create": {
            "name": "create_trace_project",
            "include_fields": ["name", "model_type", "trace_type", "source", "tags"],
        },
        "update_project_name": {
            "name": "rename_trace_project",
            "serializer": "ProjectNameUpdateSerializer",  # named override
            "method": "POST",                              # @action methods=["post"]
        },
    },
)
class ProjectView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer
```

Live-verified registered result:

```
list_trace_projects   props=['name','project_type','page_number','page_size']   category=tracing
get_trace_project     props=['id']
create_trace_project  props=['model_type','name','trace_type','source']
rename_trace_project  props=['project_id','name','sampling_rate']        (project_id from the serializer body)
```

> On `create_trace_project`: `read_only` fields are skipped, and any field whose DRF class is absent from `DRF_FIELD_TYPE_MAP` (e.g. `ManyRelatedField`) is dropped. Note that a plain `PrimaryKeyRelatedField` is **kept** and mapped to `str` — it is in `DRF_FIELD_TYPE_MAP`; the `_is_relational` guard only suppresses `.choices` enumeration, it does not drop the field. So the visible props reflect read-only filtering plus unmapped-field-type drops, not "all relational fields removed."

### Variations

**(a) Custom `@action` — detail vs list.** A `@action` is just another `tools` key.
- `detail=False` (collection action): the bridge does NOT inject an `id`; any record id must come from the action's own serializer. `update_project_name` (a `detail=False` POST action) resolves to `['project_id', 'name', 'sampling_rate']` — `project_id` comes from `ProjectNameUpdateSerializer`, not the bridge.
- `detail=True` (record action): the bridge injects the record `id` and routes it to the URL kwarg (~883). A ModelViewSet detail `@action` whose handler takes a *named* kwarg (e.g. `assign_items(request, queue_id=...)`) needs `pk_kwarg="queue_id"` so the id routes there instead of bare `pk`.

> ⚠️ A custom action name (`"execute"`, `"submit"`) is **not** in `ACTION_METHOD_MAP` / `DETAIL_ACTIONS`, so the bridge can't infer `method`/`detail`. **You MUST set them by hand**, or the tool defaults to GET/non-detail and breaks. Verified: `execute_run_test` (action `"execute"`, config `method:"POST", detail:True, pk_kwarg:"run_test_id"`).

**(b) `partial_update` / PATCH.** PATCH is first-class: `partial_update` is in both `WRITE_ACTIONS` and `DETAIL_ACTIONS`, and `ACTION_METHOD_MAP["partial_update"] = "PATCH"`. Bridging a partial update needs no manual `method`/`detail` (both are inferred), and the detail `id` is auto-injected:

```python
"partial_update": {"name": "patch_trace_project"},   # method PATCH + detail id auto-injected
```

**(c) APIView (verb-based, functional form).** For a view you don't want to edit in place, call the decorator as a function in a `bridge/_xxx.py` module:

```python
from ai_tools.drf_bridge import expose_to_mcp
from simulate.views.run_test import RunTestListView

expose_to_mcp(
    category="simulation",
    tools={"list": {"name": "list_run_tests", "entity": "run test", "query_params": {...}}},
)(RunTestListView)
```

APIViews have no `.list()`/`.retrieve()` — handlers are named by HTTP verb. `_get_action_serializer` treats the `action` as a hint and falls back to the verb handler for serializer discovery; `_resolve_apiview_handler` does the same mapping at call time. Because there's no sibling list tool to auto-discover, set `id_source` explicitly so the LLM is told where the id comes from.

**(d) Write body from a `@validated_request` serializer.** For an APIView whose `.post` is wrapped with `@validated_request(request_serializer=CreateRunTestSerializer)`, write **no** serializer config — it's auto-recovered (resolution order below):

```python
expose_to_mcp(
    category="simulation",
    tools={"create": {"name": "create_run_test", "entity": "run test", "description": "..."}},
)(CreateRunTestView)
```

Verified: `create_run_test` → `['name','description','agent_definition_id','scenario_ids','dataset_row_ids','eval_config_ids','evaluations_config','enable_tool_evaluation','replay_session_id','agent_version']`.

**(e) Id injection.** For `retrieve`/`destroy`, the bridge auto-builds an `id`-only input with a "How to get it" hint pointing at the list tool (~839). For other detail actions it injects `pk_field` on top of the serializer fields (~883). For an APIView whose handler takes a view-specific kwarg, set `pk_kwarg`:

```python
"retrieve": {"name": "get_run_test", "pk_kwarg": "run_test_id",
             "id_source": "list_run_tests", "entity": "run test"}
```

The injected `id` is popped in `execute()` and routed to `kwargs[pk_kwarg]` (~640).

### Serializer/handler resolution order (`_get_action_serializer`, ~562)

For write/create actions the serializer is found in this exact order — set the lowest-priority you can get away with:

1. Explicit `"serializer": "Name"` config key (resolved by import-path **guessing** — see anti-pattern §7.5).
2. `@swagger_auto_schema` metadata on the method — `request_body` if a class, else `query_serializer`.
3. **Closure inspection** of `@validated_request` via `_extract_serializer_from_validated_request` — walks `wrapper.__closure__` for the first `SerializerMetaclass`. (`validated_request`, defined at `tfc/utils/api_contracts.py:247`, both wraps in a closure capturing `request_serializer`/`query_serializer` **and** applies `@swagger_auto_schema(request_body=...)` at `api_contracts.py:293` — so steps 2 and 3 are two independent recovery paths for the same serializer.)
4. Fallback to `viewset.serializer_class`.

For APIViews, if no attribute matches the action name, the resolver re-fetches the handler by HTTP verb (`method.lower()`) so a write APIView's `request_serializer` is still discovered (~607).

### Naming conventions (`_derive_tool_name`, ~123)

- **Derived name = `{verb}_{entity}[s]`, with NO category prefix.** (The old doc's `{category}_{verb}_{entity}` claim is **wrong** — `internal-docs/drf-mcp-bridge.md:126` literally states that pattern, but the live registry shows no category prefix. Trust the code.)
- Verb from `ACTION_TOOL_PREFIX`: `list→list`, `retrieve→get`, `create→create`, `update/partial_update→update`, `destroy→delete`; APIView verbs map similarly. Override per decorator with `verb_map` (e.g. `verb_map={"retrieve": "fetch"}` → `fetch_user`).
- Entity from `_derive_entity_name`: strips the suffixes `APIView`/`GenericViewSet`/`ViewSet`/`View`, then `List`/`Detail`/`Operations`, then CamelCase→snake with acronym handling (`APIKey`→`api_key`, `TTSVoice`→`tts_voice`). Override with `entity`.
- The derived name is built by `f"{prefix}_{entity_name}{suffix}"`: the pluralizing `s` is appended (only for `list` / GET-without-detail), and the `entity` string is used **verbatim** — including any spaces. So `entity="run_test"` derives `list_run_tests`, but `entity="run test"` derives the literal `"list_run tests"` (space retained). Pass an underscored entity, or just set `name` explicitly.
- **Almost every real tool sets `name` explicitly** (`list_trace_projects`, not derived `list_projects`) for a readable, intent-matching name. Name it for the words a user would actually type — Falcon scores name-word overlap (§8 D2). Keep names unique (MCP client name limits) — `registry.register` raises `ValueError` on a name collision with a different class.

> ⚠️ **`verb_map` has no in-repo example.** It is read by the decorator and behaves as documented in code, but **no bridge module, view, or test in the repo exercises it.** The `verb_map={"retrieve": "fetch"}` example above is derived from the code path, not from a working usage — verify the resulting name with the §8 registry check before relying on it.

---

## 5. `@expose_to_mcp` config reference

**Decorator signature** (`drf_bridge.py` ~940):

```python
def expose_to_mcp(category: str, tools=None, verb_map: dict | None = None):
```

| Arg | Type | Meaning |
|---|---|---|
| `category` | **required** str | Tool group written onto every generated tool. Drives `registry._categories`, Falcon proactive loading, and MCP usage/scope attribution (§10). |
| `tools` | `None` \| list \| dict | `None`→`STANDARD_CRUD`; list→cherry-pick action names; dict→`action → config`. A config value may be a bare string, treated as `{"name": <string>}`. |
| `verb_map` | dict (optional) | Per-decorator override of action→verb prefix, e.g. `{"retrieve": "fetch"}`. No in-repo usage — verify before relying on it. |

**Per-tool config keys** — this is the **complete, closed set of 12** (every key actually read in `_register_bridge_tool`). Do not invent a 13th:

| Key | Effect |
|---|---|
| `name` | Explicit tool name; overrides auto-derivation. |
| `description` | Explicit description; overrides auto-derivation (which reads serializer `__doc__`). |
| `entity` | Overrides the snake-singular entity derived from the class name. |
| `method` | HTTP method override (defaults from `ACTION_METHOD_MAP`). **Required for custom actions.** |
| `detail` | bool; record-scoped action (defaults to `action_name in DETAIL_ACTIONS`). **Required for custom detail actions.** |
| `pk_field` | Input field carrying the record id (defaults `"id"` when `detail`). |
| `pk_kwarg` | The **URL kwarg name** the handler expects (`run_test_id`, `queue_id`, `call_execution_id`…). Routed to at execute time. |
| `serializer` | Explicit serializer class **name** (string) to drive the input schema. |
| `query_params` | dict `param → {type, required, description, default}` (or `param → str` shorthand) for GET/filtered tools. |
| `include_fields` / `exclude_fields` | Allowlist/denylist passed to `_serializer_to_pydantic`. |
| `id_source` | Name of a sibling tool to cite in the id field's "How to get it" hint (for APIViews with no auto-discoverable list tool). |

**Low-level escape hatch** — `viewset_tool(name, description, category, viewset_class, action, method="GET", detail=False, pk_field=None)` (~1016) takes an **explicit Pydantic input model** instead of introspecting a serializer. Manual; `expose_to_mcp` is the recommended path. Use only when there's genuinely no serializer to introspect.

**Which branch builds the input model** (`_register_bridge_tool`):
- `query_params` given → hand-built model from config (still injects `pk_field`/`id` for detail).
- list / GET-collection with no `query_params` → fixed `{search, page, page_size}` optional model (deliberately **not** the create-shaped serializer).
- detail `retrieve`/`destroy` → single `id: str` model with the rich hint.
- otherwise → serializer-derived via `_serializer_to_pydantic`.
- then, any detail action whose model lacks `pk_field` → subclassed to inject the id field.

---

## 6. Patterns (DO)

Each item names the *why* and ties to a real commit or guard. (The Golden Rule, §1, underlies all of them.)

1. **Bridge an existing view; never hand-write a parallel tool.** One source of truth; hand-written versions drift and get reverted. (`1cbc78d15`, `47de10cdc`, `ea87bf600`.)
2. **Make serializer `__doc__` + field `help_text` the single source of descriptions.** The bridge composes `{verb} {entity}. {serializer.__doc__}` and each param from `help_text`. Improve the DRF layer once → flows verbatim into every consumer. (`239657de5`, `d2a74781a` — as of that commit, the thin-tool count dropped 148→8.)
3. **Reuse the view's own `@validated_request`/`@swagger_auto_schema` serializer — don't re-declare it in config.** Zero duplication; the agent gets the real contract. APIView write handlers are named by verb, so the resolver falls back to the verb handler. (`306526d48`, `82cfa2028`.)
4. **Enumerate ONLY static `ChoiceField` enums into a `Literal`; never relational fields.** A static enum tells the LLM exactly which values are valid; the `_is_relational` guard prevents materializing a related table. (`fb3faa933`.)
5. **Inject the record id into detail-action inputs AND route it to the correct lookup kwarg.** Two requirements: the id must be *settable* (serializer-derived detail inputs exclude the read-only id) and must *reach the view* (`get_object()` reads `self.kwargs[lookup_url_kwarg or lookup_field]`; named kwargs need `pk_kwarg`). (`fa2e64787`, `8e0b4c43b` #2/#5.)
6. **Give list/GET-collection tools optional-only filters, not the create serializer.** The model serializer carries required create-fields, so `list_x({})` would fail validation demanding `name`. (`8e0b4c43b` #1 — as of that commit, fixed ~37 list tools.)
7. **Put "how to get the id" guidance IN the description.** It's the carrier that survives into whatever discovery surface the LLM sees; embedding "call `list_X` first and copy the id" lets the model chain the prerequisite call. APIView/export detail tools need explicit `id_source`. (`420bd830d`; un-truncation `da60ec49c`/`7c68fb007`.)
8. **Handle non-DRF responses (CSV/FileResponse) by extracting the body as text.** DRF `Response` has `.data`; `FileResponse`/`HttpResponse` don't — naive unwrapping collapsed them to `{}`. Render nested dict/list values as truncated JSON so eval-config `mapping`/`config` stay visible. (`93b911b37` — TH-5415/5386/5442.)
9. **Chain TRUE composites; let the LLM join individual API tools.** Pre-joining 3+ models hides structure and maps to no endpoint. A 1:1 surface (PostHog/Anthropic pattern) lets the model chain. (`b6833e4fa` — deleted 64 composites.) *Boundary:* atomic multi-model ops are NOT composites (§7.4b).
10. **Colocate `@expose_to_mcp` on the view when the file is lint-clean; use a separate `bridge/` module otherwise.** Colocation keeps MCP exposure next to the contract, but in practice the separate-`bridge/`-module form is the default (3 colocated vs ~61 programmatic) — the colocated form is reserved for legacy/lint-debt files (e.g. model_hub's 4000-line prompt_template view). (`c46023de2`.)
11. **Steer the LLM with sharper `help_text` — including inferring a default the LLM tends to get wrong.** `help_text` shapes choices, not just docs. Falcon left `output_type` at the `pass_fail` default for scored evals; the field description was sharpened to steer `'percentage'` for graded evals, with inference respecting `model_fields_set`. (`335aa6a9a` — TH-5254.)

---

## 7. Anti-patterns (DON'T)

Each has the real consequence and the right fix, tied to a fixed bug.

1. **DON'T enumerate a relational field's `.choices`.** People loop over `field.choices` for any field with choices. `PrimaryKeyRelatedField`/`ManyRelatedField`/`ModelChoiceField` build `.choices` by materializing the ENTIRE related queryset → millions of rows → OOM, plus a DB query at *import time* that breaks on schema drift. **Fix:** guard `hasattr(field, "queryset") or hasattr(field, "child_relation")`; relational fields are already mapped to `str`. (`bd6bf0c09`; `_is_relational` guard ~485.)
2. **DON'T ship a detail/`update_*` tool whose input has no id field.** Building the input straight from the serializer excludes the read-only id, so `pk_field` is required-but-unsettable — every bridged `update_*` was uncallable with "id is required." **Fix:** inject `pk_field` for any detail action that lacks it. (`fa2e64787`; ~883.)
3. **DON'T route a detail id only to `pk`.** `get_object()` reads `self.kwargs[lookup_url_kwarg or lookup_field]`; ViewSets with `lookup_field="id"` (PersonaViewSet) and named-kwarg handlers (`run_test_id`, `queue_id`) never receive it → "Expected keyword argument named 'pk'". **Fix:** set the id under `pk` AND the lookup field; use `pk_kwarg` to name the URL kwarg. (`8e0b4c43b` #2/#5; ~640.)
4. **DON'T over-bridge a view that needs hand-written logic, and DON'T delete atomic ops as "composites."** Two over-reaches:
   - (a) Slapping `@expose_to_mcp` on a view that reads *non-serializer* `request.data` fields and returns a custom envelope (`EvalGroupView` reads `eval_template_ids`). Serializer introspection can't see those fields, so `create_eval_group` couldn't accept them. **Fix:** keep it hand-written — outside the clean-CRUD boundary. (`e4bb0c7cd`, caught by `TestCreateEvalGroupTool`.)
   - (b) Deleting `create/update/delete_eval_template` in a composite sweep — they touch EvalTemplate + EvalTemplateVersion but are **atomic** eval-creation ops, not composites of separately-callable tools. Deleting them broke "create an eval from Falcon" with no replacement. **Fix:** restore; "multi-model" ≠ "composite." (`d5c29c34c`, over-applying `b6833e4fa`.)
5. **DON'T use the model serializer as a list tool's input schema.** It carries required create-fields, so `list_X({})` fails Pydantic validation. A live sweep at the time found only ~20/94 bridges actually returned data — the rest were stubs that errored at call time. **Fix:** list tools without `query_params` take only optional `search/page/page_size`. (`8e0b4c43b` #1; ~810.)
   *Related override pitfall:* the `serializer` config key is resolved by import-path **guessing** (`.views`→`.serializers`, view submodule, then a hardcoded fallback list `project/dataset/user/workspace/prompt_template`). A serializer in an unlisted submodule **silently fails to resolve** → the tool gets 0 params. Put the serializer where the bridge looks. (~581.)
6. **DON'T leave serializer fields without `help_text` or serializers without a docstring.** The bridge falls back to title-cased field names ("Name") and a generic "Create a new <entity>." — useless for tool/param selection (as of `d2a74781a`, 148 of 384 tools were thin). **Fix:** add accurate `__doc__` + `help_text` (descriptions only — don't alter types/required/write_only). (`d2a74781a`, `01264c31f`, `91114290e`.)
7. **DON'T point a write tool at the *response* serializer.** Letting `create` fall back to a response-shaped `serializer_class` gave `create_agent` a 0-field schema — Falcon couldn't pass anything. **Fix:** point create/update at the request serializer via the `serializer` override. (`c56f821b0` — `create_agent` now exposes 19 fields.)
8. **DON'T collapse non-DRF responses to `{}`.** Unwrapping every response via `response.data` loses CSV/`FileResponse` bodies. **Fix:** detect missing `.data`, read `streaming_content`/`content`, decode to text. (`93b911b37`.)
9. **DON'T bury prerequisite-id guidance past the first sentence.** Truncating descriptions to sentence 1 cut off "provide the id from `list_X` first," so the model ran the tool without the id and failed end-to-end. **Fix:** surface the full description (capped ~500 chars) + each required param's description + a chaining footer. (`da60ec49c`, `7c68fb007`.)

> ⚠️ **Silent-failure note (applies to all of the above):** `expose_to_mcp`'s per-action loop wraps each registration in `try/except` that logs `bridge_registration_failed` and continues. A misconfigured tool does **not** raise — it just fails to register, or registers with the wrong schema. This is why the §8 registry check is mandatory, not optional.

---

## 8. Testing & verification

> Run inside the backend container (Django + registry loaded). Substitute your container name for `ws1-backend`. The compose service is defined in `docker-compose.ws1.yml` — find the live container with `docker compose -f docker-compose.ws1.yml ps`. Note on this machine: there is no usable host Python; everything runs via `docker exec ws1-backend ...`. Code is volume-mounted, so a **fresh `manage.py shell` re-imports from disk and picks up your edit immediately** while the running Granian server keeps the stale import — that is *why* you verify with a shell, not a server restart.

**The fresh-shell reload trick (no Granian restart):**

```bash
docker exec -i ws1-backend python manage.py shell
```

**Registry check — the mandatory silent-failure catch** (`registry.get` returns `None` if the tool didn't register):

```bash
docker exec ws1-backend python manage.py shell -c "
from ai_tools.registry import registry
t = registry.get('list_trace_projects')          # your new tool name
print('NAME', t.name); print('CATEGORY', t.category)
print('PROPS', list(t.input_schema.get('properties', {}).keys()))
print('REQUIRED', t.input_schema.get('required'))
print('BINDING', t.binding.method, t.binding.detail, t.binding.pk_kwarg)
print('DESC', t.description[:120])
"
```

(`manage.py` setdefaults `DJANGO_SETTINGS_MODULE`, so no explicit env var is needed.) **Don't trust hardcoded counts in any doc — including the §1/§9 snapshots; ask the registry:** `registry.count()`, `registry.categories()`, `[t.name for t in registry.list_all() if "run_test" in t.name]`, and `len([t for t in registry.list_all() if type(t).__name__ == "DRFBridgeTool"])`. (Naive shell grep does NOT count bridges — see §9.)

**ToolDiscoveryView — the over-the-wire view** (`ai_tools/views.py`, `IsAuthenticated`). Returns every tool's `to_dict()` (name/description/category/input_schema) — exactly what Falcon/MCP see:

```
GET /ai-tools/tools/                  # all tools → {tools, categories, total}
GET /ai-tools/tools/?category=tracing
```

**Bridge unit tests (~37, fast, mocked):**

```bash
docker exec ws1-backend python -m pytest ai_tools/tests/test_drf_bridge.py -v
```

> ⚠️ **Two failure modes the unit tests CANNOT catch:**
>
> **D1 — Green unit tests, broken live call.** `test_drf_bridge.py` **mocks the ViewSet** (`patch("ai_tools.drf_bridge._resolve_class", ...)`), so it stays green even when the real dispatch fails — wrong `pk_kwarg`, schema drift, a list tool inheriting required create-fields. The first live sweep found only **20/94** bridges working; after fixes, ~85. (Those ratios reflect the 94-bridge era; with 252 bridge tools today, rerunning the sweep won't reproduce `94` — read them as "as of that sweep.") "Registered" ≠ "works." **Always run the live sweeps:**
>
> ```bash
> docker exec ws1-backend python -m ai_tools.tests.verify_bridges   # read-only: every list/get bridge vs live DB
> docker exec ws1-backend python -m ai_tools.tests.verify_writes    # write round-trip (create→delete, net-zero)
> ```
> For a new write tool, add a `(create_tool, minimal_args, delete_tool)` tuple to the `ROUNDTRIPS` list in `verify_writes.py`.
>
> **D2 — Registered and callable, but Falcon never selects it.** Falcon filters to ~40 tools before the LLM sees them (`filter_tools_for_message`, `ee/falcon_ai/modes.py`), scoring name-word overlap with the user message (+5/word) plus a same-`category` boost (+3). A poorly-named tool loses the relevance race and gets dropped. Fix the `name` (words a user would type), the `category`, and the `description` (also scored). Bench real selection:
>
> ```bash
> docker exec ws1-backend python -m ai_tools.tests.bench_falcon_bridge   # selection accuracy, latency, which tool was picked
> ```

---

## 9. Current status & TODO / backlog

> **The brief's premise is stale — read this first.** The mission named "the simulate run-test / scenario create+execute lifecycle" as the biggest unbridged gap. **It is already bridged.** `ai_tools/tools/bridge/_simulate.py` (commit `82cfa2028`) wires the full happy path: `create_scenario` → `create_run_test` → `execute_run_test` → `get_test_execution_status`, plus the read/analytics surface (`list/get_run_test`, `get_run_test_analytics`, `get_eval_explanation_summary`, `get_fix_my_agent_analysis`, `export_test_execution_csv`, `get_call_execution`, `get_scenario`). **Do NOT re-list the lifecycle as TODO.** What the simulate *cluster* still carries is a lower-value hand-written tail (§ cluster #3), not the lifecycle.

### Status snapshot (registry is the source of truth; registry-verified 2026-06-04; supersedes the stale doc)

| Metric | Value | Stale doc said |
|---|---:|---|
| Total tools registered | **393** live (390 in a bare shell; +3 EE memory keepers from `FalconAIConfig.ready()`) | — |
| **Bridge tools** (`DRFBridgeTool`) | **252** | ~~244~~ |
| Hand-written (`@register_tool`) | **141** live (138 in a bare harness, before the +3 memory keepers) | ~~"126 remaining"~~ (a file-bucket count, not registry truth) |
| Distinct view classes bridged | **~64** | ~~"14 ViewSets"~~ (only counted the literal `@` form) |
| Bridge modules | **13** domain modules (`ai_tools/tools/bridge/_*.py`, excluding `__init__.py`) | — |

> **Why naive grep misleads:** `grep -rn '@expose_to_mcp' --include='*.py' .` (quote the glob in zsh or it errors with "no matches found") returns **~14**, but that counts docstrings/comments/tests — only **3 are real `@`-on-class decorations**, across **2 files** (`tracer/views/project.py` ×1, `accounts/views/workspace_management.py` ×2). The decorator is applied programmatically `expose_to_mcp(...)(Class)` for ~61 of ~64 bridged classes, so grep does **not** count bridges. **To count bridges, use the registry, not grep.**
>
> **Baseline caution:** the bridge counts above are the live (393-tool) baseline; the per-category spine below sums its hand-written side from the 138-tool harness baseline (the +3 EE memory keepers land in category `context`, not loaded in the bare harness). Pick one baseline when re-deriving; don't mix them.

Bridge vs hand-written by category (the backlog spine; hand-written side = 138-harness baseline): `agentcc` 80/0 · `tracing` 52/18 · `datasets` 25/12 · `prompts` 20/13 · `simulation` 15/21 · `evaluations` 10/23 · `annotation_queues` 16/0 · `annotations` 9/3 · `agents` 8/3 · `users` 7/9 · `optimization` 5/9 · `experiments` 5/8 · `context/web/docs/usage/visualization/error_feed` 0/22 (keepers — context 8 incl. the 3 EE memory tools + web 4 + docs 3 + usage 1 + visualization 1 + error_feed 5).

> Note: the **141 hand-written** tools are NOT all keepers. Only **~16 + 2** (below) are true keepers; the other ~123 are the **backlog** in the prioritized clusters.

### Prioritized clusters (value × bridgeability, NOT raw count)

Buckets: **(A)** bridge `@action`s on an already-CRUD-bridged ViewSet (cheapest, highest-leverage — the root-cause gap); **(B)** bridge an existing-but-unbridged APIView/ViewSet; **(C)** API must be built first.

1. **(A) Prompt-template `@action` surface — top quick win.** `model_hub/views/prompt_template.py :: PromptTemplateViewSet` has CRUD bridged but **~17 custom `@action`s unbridged** (`compare-versions`, `all-variables`, `get-next-version`, `add-new-draft`, `create-draft`, `evaluations`, `get-run-status`, `evaluation-configs`, `update-evaluation-configs`, `delete-evaluation-config`, `run-evals-on-multiple-versions`, `get-template-by-name`). These back the 13 hand-written `prompts/` tools. One concentrated target, genuinely bridgeable via explicit action+serializer config.
2. **(A/B) Experiments read/stats.** 8 hand-written, mostly backing existing APIViews in `model_hub/views/experiments.py` (`ExperimentListAPIView`, `ExperimentStatsView`/`V2`, `ExperimentEvaluationStatsView`, `ExperimentDatasetComparisonView`, `ExperimentsTable/List/DetailView`). Bridge as detail/list APIViews with `pk_kwarg=experiment_id`. Caveat: `create_experiment` calls `experiment_service` directly — confirm a POST APIView exists or it's (C).
3. **(A) Simulation cluster tail (lifecycle already done).** 21 hand-written remain: simulator-agent CRUD, `activate/compare_agent_version`, `duplicate/delete_agent_definition`, scenario `update/delete_scenario`, simulate eval-config CRUD, `get_call_logs`/`get_call_transcript`, `get_test_execution_analytics`, `cancel/delete_test_execution`, `rerun_call_execution`. Many map to existing `simulate/views/*` APIViews. `create_simulator_agent` does direct ORM — verify a create APIView or treat as (C).
4. **(A) Tracing remainder.** 18 hand-written span/tag/eval-task/widget tools; several map to `@action`s on the already-bridged `TraceView`/`EvalTaskView`. `render_widget` is a keeper (visualization). Review `search_traces`/`search_trace_spans` for overlap with the context `search` keeper before bridging.
5. **(B/C) Evaluations — split, don't rank by volume.** 23 hand-written is the biggest raw count but **inflated by ORM-direct tools.** Bridgeable-to-existing-API: eval-template create/update via `model_hub/views/eval_runner.py` and the metric APIViews in `model_hub/views/metric.py`. **(C) build-first:** `list_eval_templates`/`list_evaluations` read `EvalTemplate.no_workspace_objects` ORM directly — no clean list APIView. Eval-group tools are deliberate keepers (below).
6. **(C) Optimization.** 9 hand-written, mostly direct ORM (`OptimizeDataset.objects`, trials). Existing views are thin; `get_optimization_graph/steps/trial/*` have no list/detail API → NEW_ENDPOINT_NEEDED. Lower priority.
7. **(B) Users / workspace-management.** 9 hand-written (`add_workspace_member`, `invite_users`, `update_user_role`, `update_workspace`, `list/get_organization(s)`, `get_user_permissions`, `deactivate_user`, `list_workspace_members`). `accounts/views/workspace_management.py` already hosts the 2 `@`-bridged list views; extend with the member/role/invite APIViews. RBAC-sensitive — verify perms per tool (§11 authorization caveat).
8. **(B) Annotations / agents tails.** Small (3 + 3): operations not covered by `AnnotationsViewSet`/`AgentDefinitionOperationsViewSet`. Lowest value; finish last.

> **Methodology warning:** these are **per-tool** work, not a batch. Each needs its exact target action, `pk_kwarg`, and serializer wired, then verified against the live DB (`verify_bridges.py`). Blind registration produced 20/94 call-time-broken stubs the first time.

### Custom keepers — DO NOT bridge (~16 + 2 deliberate)

**External / no-REST-analogue (16):**
- **context/**: `whoami`, `search`, `read_schema`, `read_taxonomy` (+ `search_tools`, the discovery lever)
- **web/**: `web_search`, `search_ground_truth`, `search_knowledge_base`, `explore_trace`
- **docs/**: `ask_docs`, `search_docs`, `get_docs_page`
- **usage/**: `get_cost_breakdown` · **visualization/**: `render_widget`
- **falcon EE memory** (registered at category `context`): `save_memory`, `list_memories`, `delete_memory`

**Two deliberate non-bridges (keep hand-written for a code reason):**
- **`eval_group` tools** — `EvalGroupView.create/update` read **non-serializer fields off `request.data`** (`eval_template_ids`, `added_template_ids`, `deleted_template_ids`). Serializer introspection has nothing to bind. Keep hand-written until the view takes a real request serializer. (Documented in `ai_tools/tools/__init__.py`.)
- **`error_feed` tools** (5: `list_error_clusters`, `analyze_error_cluster`, `get_error_cluster_detail`, `get_trace_error_analysis`, `submit_trace_finding`) — service/ClickHouse-backed analytics dicts, not serializer-backed model CRUD. Treat as keepers / NEW_ENDPOINT.

---

## 10. Integration — MCP server + Falcon

Everything hangs off a single module-level singleton, `ToolRegistry` (`ai_tools/registry.py`), indexed by `name` (`_tools`) and `category` (`_categories`). Both consumers read this same object; neither keeps its own catalog. Registration runs once during `django.setup()` via `AiToolsConfig.ready()` → `import ai_tools.tools` → every bridge module's decorators fire.

**Discovery (`ToolDiscoveryView`, `/ai-tools/tools/`, `IsAuthenticated`).** A purely introspective read-model over the registry (`list_all()` / `list_by_category()` → `to_dict()`). It is **not** how either agent loads tools at runtime — both call the singleton in-process. It's the catalog you hit to confirm a newly-bridged tool registered.

**MCP server (`mcp_server/mcp_app.py`).** A stateless Streamable-HTTP `FastMCP` server at `/mcp`. At import, `_register_ai_tools()` iterates `ai_registry.list_all()` and registers a FastMCP handler per tool via `mcp.tool(name, description)(handler)`. The handler builds an `inspect.Signature` from the tool's Pydantic `input_model.model_fields` so FastMCP derives the **same** JSON Schema the bridge generated — one schema source. A newly-bridged tool is advertised automatically, no per-tool MCP wiring. Timing is safe: `mcp_app.py` is imported lazily on the first `/mcp` request (via `asgi.py`'s `_get_mcp_app()`), long after `django.setup()`.
- *Visibility vs attribution:* the live server advertises **all** registered tools to any authenticated client. `CATEGORY_TO_GROUP`/`TOOL_GROUPS` (`mcp_server/constants.py`) are used **only** for usage attribution (`record_usage` tags `tool_group`) and as the OAuth-scope catalog — they do **not** filter the tool list. The per-connection `MCPToolGroupConfig` (`enabled_groups`/`disabled_tools`, dashboard surface) is **not read** by `mcp_app.py` — verified — so that enable/disable config is a dashboard/UX surface not yet enforced at the live tool-list layer.

**Falcon AI (`ee/falcon_ai/agent.py`).** Falcon derives a tool set per turn from the registry:
- `detect_mode()` + `load_tools_for_mode(mode)` build candidates from `CORE_TOOLS`/`COMMON_TOOLS` (by name) + each mode's `MODES[mode]["categories"]` (via `list_by_category`).
- `filter_tools_for_message()` caps the slate at ~40 (FILTER_CORE + recently-used + keyword-scored); only these go to the LLM with full schemas. The full candidate set is retained as `self._all_tools`.
- **`search_tools`** (`ai_tools/tools/context/search_tools.py`, always loaded) scans **`registry.list_all()` — the FULL registry** — ranking by name/description/param-name/category/verb, returning exact names + params. This makes the whole catalog reachable without paying per-turn schema cost.
- **Auto-load on call** (~458): the loop's first lookup is `tool_registry.get(tool_name)` against the global registry; if found but not active, it's appended and the OpenAI-tools cache invalidated. So `search_tools` makes the model *aware*; the registry lookup makes it *callable*.

**Two tiers of reachability for a newly-bridged tool:**
- **Callable: nothing required.** `search_tools` scans the full registry and the loop resolves any call via `registry.get`. Discoverable and runnable the instant it's registered — zero Falcon code changes.
- **Proactively loaded (in the initial ~40 without a `search_tools` call): gated by `category`.** `load_tools_for_mode` only pulls a tool proactively if its `category` is in some mode's categories (i.e. in `ALL_CATEGORIES`) or its name is in `CORE_TOOLS`/`COMMON_TOOLS`/`FILTER_CORE_TOOLS`. **Real consequence today:** tools in categories `agentcc` and `annotation_queues` are **search-only in Falcon** — neither category is in `ALL_CATEGORIES`, so they never appear in any mode's proactive set. To surface proactively, give the tool a category already in `ALL_CATEGORIES` (`simulation`, `tracing`, `evaluations`, `datasets`, `prompts`, `users`…) or add its category to `ALL_CATEGORIES`/the relevant mode.

*(Note: `PromptBuilder._tools()` only emits "You have N tools loaded; additional tools auto-load if you call them by name." The actual-names discovery channel is `search_tools`. An inline comment in `agent.py` about "deferred tool names in system prompt" is stale.)*

### "No changes required to" — what you DON'T touch

When you bridge a tool on an **already-imported** view:
- `ai_tools/registry.py` — registration is generic.
- `ai_tools/views.py` (`ToolDiscoveryView`) — reads `list_all()`/`list_by_category()`.
- `mcp_server/mcp_app.py` / `_register_ai_tools()` — iterates the whole registry.
- `ee/falcon_ai/agent.py` (loop, `search_tools`, auto-load) — generic.
- `ee/falcon_ai/prompt_builder.py` — emits a count + the auto-load contract, not names.
- `tfc/asgi.py` / `tfc/urls.py` — routing is fixed.

### What you DO touch (mandatory prerequisites)

1. **Import wiring** — required only if the view is in a **new** module: add the import to `ai_tools/tools/__init__.py` (or add the `bridge/_xxx` module to the `from ai_tools.tools.bridge import (...)` block). Bridging an already-imported view = zero wiring.
2. **`category` choice** — the **single downstream lever**: it decides Falcon proactive loading (`ALL_CATEGORIES`/`MODES`) and MCP usage/OAuth-scope grouping (`CATEGORY_TO_GROUP`). Raw reachability (Falcon `search_tools` + MCP advertise-all) is category-independent; proactive Falcon loading is not.

---

## 11. Troubleshooting / FAQ + decision flowchart

### Decision flowchart — "I need a new agent capability"

Top to bottom; stop at the first match.

```
1. Does a DRF ViewSet/APIView already do this operation?
   └─ YES → BRIDGE IT. expose_to_mcp(category, tools={...}) in a bridge/_xxx.py module
            (or @ on the view if lint-clean) + import in tools/__init__.py. Done.
            (Pattern: tracer/views/project.py; default form: ai_tools/tools/bridge/_*.py)

2. An API exists but is wrong for an agent? (no help_text, missing a filter,
   a @action that reads request.data directly instead of a serializer)
   └─ IMPROVE the DRF layer, THEN bridge. Add help_text / @validated_request(query_serializer=…)
      / query_params, then decorate. Don't patch around it in a custom tool.

3. No Django view exposes the operation at all? (today only direct ORM)
   └─ BUILD the DRF APIView + serializer first, then bridge it. (NEW_ENDPOINT_NEEDED bucket.)

4. Genuinely bespoke, no single REST analogue? (external service, fan-out across
   several APIs, heavy LLM-side formatting / name→UUID resolution)
   └─ JUSTIFIED CUSTOM KEEPER (the ~16, §9). If it isn't shaped like one of those, it's 1–3.

Default: unsure between "improve + bridge" and "custom keeper" → it's almost always
improve + bridge. A custom tool needs a written no-REST-analogue justification to pass review.
```

### FAQ — common failures and fixes

**F1 — Tool doesn't appear in the registry.** The decorator never ran. `@expose_to_mcp` only fires when the view module is imported (via `AiToolsConfig.ready()` → `tools/__init__.py`). **Fix:** add `import myapp.views.thing  # noqa: F401` (or add the `bridge/_yourmodule` to the import block). Remember registration failures are swallowed and logged (`bridge_registration_failed`), not raised — a view that imports fine can register zero tools. Check the registry (§8).

**F2 — Input schema empty or missing fields.** Possible causes: the field is `read_only` (intentionally skipped, along with `organization`/`workspace`/`deleted`/`deleted_at`/`created_at`/`updated_at`); the field type isn't in `DRF_FIELD_TYPE_MAP` (silently dropped — `type_info is None → continue`, ~465 — e.g. `ManyRelatedField`); only skipped fields survive → `EmptyInput`; or it's a list tool with no `query_params` (gets only optional `search/page/page_size` by design — F4/§7.5).

**F3 — Import-time OOM or DB error at registration.** Enumerating a **relational** field's `.choices` materializes the whole related queryset (OOM + a live DB query at import time). Already guarded by `_is_relational`; keep the guard if you add custom field handling. Relational FKs are mapped to `str` — never enumerate them. (§7.1)

**F4 — `update_*` / detail `@action` uncallable ("X is required" but no input field for it).** A detail action needs the id in its input but a serializer-based detail action exposes no `id`. Already handled: `_register_bridge_tool` injects `pk_field` (~883). At call time `execute()` routes it: standard CRUD → `pk` + `lookup_field`/`lookup_url_kwarg`; APIView/custom `@action` with a named kwarg → **you must set `pk_kwarg`**, else the id goes to `pk` and the handler rejects it. See every `retrieve`/`execute` block in `_simulate.py`. (§7.3)

**F5 — Description is useless ("Name", generic verb).** No `help_text` → per-field description falls back to `field_name.title()`; no serializer/view docstring → tool description is just `"{verb} {entity}s."`. **Fix:** add `help_text=` + a one-line serializer docstring (or pass explicit `description`). `_derive_description` reads `serializer.__doc__` → `viewset.__doc__` → generic. Put constraints in `help_text`. (§7.6)

**F6 — Wrong serializer picked for a custom action.** Misreading the `_get_action_serializer` order (§4): (1) explicit `"serializer"` config → (2) `@swagger_auto_schema` metadata → (3) `@validated_request` closure inspection → (4) `viewset.serializer_class`. **Fix:** set `"serializer"` explicitly (highest priority). For APIViews the handler is named by verb, not action — if a write APIView gets 0 params, the serializer lives on `.post` and the verb-handler fallback (~607) resolves it; custom action names still need a config hint.

### Two gotchas the unit tests can't catch (recap)

- **Unit-green, live-broken (D1):** `test_drf_bridge.py` mocks the ViewSet. Always run `verify_bridges.py` (read) and `verify_writes.py` (write) — they're the only proof the real DRF call succeeds.
- **Registered but never selected (D2):** Falcon filters to ~40 by name-word overlap (+5/word) + category boost (+3). Name the tool for the words a user would type, set the right `category`, write a real `description`; `bench_falcon_bridge.py` is the real selection signal.

### Authorization caveat (read before bridging anything permission-sensitive)

The bridge **never calls** `view.dispatch()`, `check_permissions()`, `perform_authentication()`, or `initial()` — it invokes the action method directly. So the view's `permission_classes`, throttles, and content negotiation are **not enforced by the bridge.** The only access control is (a) whatever the action body itself re-checks and (b) org/workspace scoping flowing through `workspace_context` + the manually-stamped `request.user/workspace/organization` (which `BaseModelViewSetMixinWithUserOrg.get_queryset` and BaseModel managers rely on). **Authorization here is data-scoping-based, not DRF-permission-based.** If a view relies on `permission_classes` alone for object-level authorization rather than queryset scoping, the bridged tool would skip that check — verify scoping per tool when bridging RBAC-sensitive views (e.g. users/workspace-management, §9 #7).

### Quick reference — file map

| Purpose | Path |
|---|---|
| Bridge core / introspection | `futureagi/ai_tools/drf_bridge.py` |
| Registration import wiring | `futureagi/ai_tools/tools/__init__.py` |
| App-ready trigger | `futureagi/ai_tools/apps.py` |
| Per-domain bridge wirings (real `pk_kwarg`/serializer/`query_params`) | `futureagi/ai_tools/tools/bridge/` (esp. `_simulate.py`) |
| Registry singleton | `futureagi/ai_tools/registry.py` |
| Discovery endpoint | `futureagi/ai_tools/views.py` (`/ai-tools/tools/`) |
| `@validated_request` source | `futureagi/tfc/utils/api_contracts.py:247` (def); `@swagger_auto_schema` applied at `:293` |
| Falcon loop / filter / auto-load | `futureagi/ee/falcon_ai/agent.py`, `…/modes.py`, `…/prompt_builder.py` |
| Search lever | `futureagi/ai_tools/tools/context/search_tools.py` |
| MCP server / constants | `futureagi/mcp_server/mcp_app.py`, `…/constants.py` |
| Unit tests (mocked) | `futureagi/ai_tools/tests/test_drf_bridge.py` |
| Live verification | `verify_bridges.py` (read), `verify_writes.py` (write), `bench_falcon_bridge.py` (LLM selection) — under `futureagi/ai_tools/tests/` |
