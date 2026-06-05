# ADR-033: Agent template system with formal composition proofs

**Status**: Accepted  
**Date**: 2026-05-09  
**PR**: #358  
**Issue**: #32

---

## Context

Users build agent graphs in the playground and want to reuse sub-graphs across
projects. Before this ADR:

- `Graph.is_template=True` existed but only for **system templates** (no org, no
  creator — injected by migrations). Users had no way to publish their own graphs
  as templates.
- There was no API for discovering or instantiating templates.
- Embedding a template as a subgraph node required manually wiring ports and had
  no cycle guard.

The core graph-safety invariant is that composed graphs must remain DAGs. A naive
"copy-paste subgraph" approach risks introducing cycles when graphs embed each
other transitively.

---

## Decision

### Two-scope template model

`Graph.is_template=True` now covers two distinct scopes:

| Scope | `organization` | `created_by` | Visibility |
|-------|---------------|-------------|------------|
| System template | None | None | All orgs |
| Org-scoped template | Set | Set | Within that org only |

`Graph.clean()` is extended to enforce the invariant: either both `organization`
and `created_by` are set (org-scoped) or neither is (system). A workspace is
never allowed on a template.

Tags (`ArrayField(CharField(50))`) on `Graph` enable discovery filtering via
PostgreSQL `@>` containment. A join table was rejected — consistent with
`DatasetEvalConfig.filter_tags` and sufficient for current query patterns.

### Composition closure theorem

The load-bearing correctness argument:

> **G[T/n] is acyclic ⟺ G is acyclic ∧ T is acyclic ∧ adding edge (G→T) to
> the cross-graph reference DAG (crDAG) keeps it acyclic.**

This reduces the runtime safety check to a single DFS on the crDAG — exactly
what `would_create_graph_reference_cycle` implements. The theorem means we never
need to materialize the composed graph to verify safety.

```
Composed G[T/SG]:

  Parent graph G          Template T
  A → B → [SG] → D       X → Y → Z

  Inline T at SG:
  A → B → X → Y → Z → D

  Required: no back-edge from Z (or any T node) to A or B.
  Equivalent to: crDAG edge (G → T) does not form a cycle in crDAG.
```

The service layer (`template_composition.py`) is **pure Python with no Django
imports**. This keeps the composition logic testable without a running app server
and enables the formal test suite to exercise it directly.

### API

`TemplateViewSet` at `/agent-playground/templates/`:

| Method | Path | Action |
|--------|------|--------|
| `GET` | `/templates/` | List system + org templates; `?tags=rag,safety&q=search` |
| `POST` | `/templates/` | Publish existing graph as org-scoped template |
| `GET` | `/templates/<id>/` | Template detail |
| `POST` | `/templates/<id>/instantiate/` | Embed as subgraph node in a draft `GraphVersion` |

The `instantiate` endpoint:
1. Verifies the template has an active version.
2. Calls `detect_cross_graph_cycle(adj, source_graph_id, template_id)`.
3. Creates `Node(type=subgraph, ref_graph_version=active_version)`.
4. Mirrors exposed ports as `ref_port`-linked custom ports.

### Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| Org-scoped templates only | Blocks system-defined reusable blueprints |
| System templates only (status quo) | Users cannot publish their own sub-graphs |
| Separate `Template` model | Duplicates `Graph` + `GraphVersion`; split identity adds accidental complexity |
| Copy-subgraph-nodes on instantiate | O(nodes) copy; loses link to template updates; cycle check is harder |

---

## Consequences

- **Additive**: Existing system templates are unchanged — `is_template=True`
  with no org/created_by still works.
- **Migration**: `0012_graph_template_tags` — adds `tags` `ArrayField` and
  composite index `(is_template, organization)`.
- **Cycle safety**: The DFS guard is enforced at `instantiate` time. A rejected
  instantiation returns HTTP 400 with a descriptive error; no graph state is
  mutated.
- **Port mirroring**: Ports are mirrored at instantiation time. If the template's
  active version changes after instantiation, the subgraph node's ports are NOT
  automatically updated — this is intentional (version stability).
- **Discovery**: `?tags=` uses `ArrayField` containment (`@>`). A GIN index on
  `tags` should be added if the template catalog grows beyond ~1 000 rows.

---

## Formal verification

### TLA+ spec: `docs/tla/TemplateVersioning.tla`

Invariants:
- `TypeInvariant` — typed state
- `ActiveVersionUnique` — at most one active version per template
- `NoDraftExecution` — executions only reference published (active) versions
- `ExecutionVersionStability` — a running execution's version cannot change

Properties (liveness, with `WF` fairness):
- `EventuallyPublishable` — every draft eventually reaches `published` state
- `EventuallyCompletes` — every started execution eventually terminates

> **TLC note**: Not wired into CI. Run manually:
> ```
> tlc docs/tla/TemplateVersioning.tla -config docs/tla/TemplateVersioning.cfg
> ```
> Config: `Templates = {T1, T2}`, `MaxVersions = 3`, `MaxExecutions = 2`

### Z3 proofs: `agent_playground/formal_tests/test_composition_z3.py`

| Test class | What it proves |
|------------|---------------|
| `TestCompositionClosure` | G[T/n] acyclic ⟺ G, T DAGs and crDAG edge (G→T) acyclic (UNSAT) |
| `TestCrossGraphCycleDetection` | DFS soundness — if cycle exists, UNSAT |
| `TestLevelMonotonicity` | `level[src] > level[tgt]` for all crDAG edges |
| `TestPortDirectionPreservation` | Schema reflexivity; direction enum is `{input, output}` with no overlap |
| `TestInterfaceCompatibility` | Boundary type checker is sound |

### Hypothesis properties: `agent_playground/formal_tests/test_composition_hypothesis.py`

Seven properties, 100–200 examples each:

| Property | Description |
|----------|-------------|
| Acyclicity preserved | Composing two DAGs yields a DAG (200 ex) |
| No false positives | `detect_cross_graph_cycle` never flags a truly acyclic crDAG |
| Schema ≤ reflexive | `schemas_compatible(s, s)` is always `True` |
| Schema ≤ transitive | If `A ≤ B` and `B ≤ C` then `A ≤ C` |
| Single-node inline | Inlining a single-node template is identity up to node relabeling |
| Level monotonicity | Kahn BFS levels decrease along each crDAG edge |
| Empty graph compose | Composing with an empty template leaves parent unchanged |
