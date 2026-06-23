# Observe Flows — Manual Testing Guide

## ✅ Seeded Data — Verified Working via API

| What | Detail | API confirmed |
|------|--------|--------------|
| **Observe Test Project** | ID: `aaaaaaaa-0001-...` | ✅ visible |
| **Delete Me Project** | ID: `aaaaaaaa-0002-...` | ✅ visible |
| **Traces** | 10 in Observe Test, 2 in Delete Me | ✅ 10 returned |
| **Spans** | 30 in CH (3/trace: AGENT → LLM + TOOL) | ✅ tree renders |
| **Agent Graph** | 12 nodes, 20 edges aggregated | ✅ returns data |
| **Graph View** | 169 hourly data points | ✅ returns data |
| **Eval Config** | "Quality Eval" on Observe Test Project | ✅ in DB |

**Local app:** `http://localhost:3031`

---

## Flow 3.1 — Saved Views E2E

**Goal:** Create → view → update → delete a saved view scoped to a project.

### Steps
1. Open `http://localhost:3031` → go to **Observe**
2. Click into **Observe Test Project**
3. On the Traces/Spans tab, find **"Save View"** or **"+ New View"** button → click
4. Name it **"My Test View"**, visibility = Personal → Save
5. ✅ View appears in the sidebar/tabs
6. Click the saved view to load it
7. ✅ Loads without error, shows trace data
8. Edit the name → **"My Test View UPDATED"**
9. ✅ Name persists after page refresh
10. Delete the view
11. ✅ Disappears from list

**Verify via DB:**
```bash
docker exec postgres psql -U user -d tfc -c "SELECT name, visibility, tab_type FROM tracer_saved_view WHERE deleted=false ORDER BY created_at DESC LIMIT 5;"
```

---

## Flow 5.1 — Graph View

**Goal:** Main metrics graph (Latency / Tokens / Cost / Traffic) renders with data.

### Steps
1. Open **Observe Test Project**
2. Click the **Graph** or **Overview** tab
3. ✅ You see 4 metric charts — Latency, Tokens, Cost, Traffic
4. ✅ Lines/bars have actual data points (169 hourly points seeded)
5. Change time range to **Last 1 hour** → graph updates
6. ✅ Still shows data (spans are from the last 10 hours)
7. Change time range to **Last 7 days** → more data points
8. ✅ Graph adjusts, doesn't crash
9. Switch the metric dropdown between Latency / Tokens / Cost / Traffic
10. ✅ Each shows different values (cost may be 0 — that's fine)

---

## Flow 5.2 — Agent Graph

**Goal:** Aggregate call-flow graph across all traces shows correct node types and edges.

### Steps
1. Open **Observe Test Project**
2. Click the **Agent Graph** tab
3. ✅ You see nodes for: **AGENT**, **LLM**, **TOOL**, **RETRIEVAL** (all span types seeded)
4. ✅ Edges connect AGENT→LLM and AGENT→TOOL (20 edges seeded)
5. ✅ Node labels show span names (e.g. "llm-call", "tool-use", "Agent: Book flight")
6. Click a node (e.g. LLM)
7. ✅ Side panel shows trace count for that node type
8. Apply a time filter (e.g. Last 1 hour) — node counts should change
9. ✅ Graph updates without error

**API check:**
```bash
T=$(cat /tmp/token.txt)
curl -s "http://localhost:8000/tracer/trace/agent_graph/?project_id=aaaaaaaa-0001-0000-0000-000000000000" \
  -H "Authorization: Bearer $T" | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['result']; print('nodes:', len(r['nodes']), '| edges:', len(r['edges']))"
# Expected: nodes: 12 | edges: 20
```

---

## Flow 5.3 — Agent Path (Trace Detail)

**Goal:** A single trace shows the full span tree — parent and child spans in the correct hierarchy.

### Steps
1. Open **Observe Test Project** → **Traces** tab
2. Click trace **"Agent: Book flight"**
3. ✅ Trace detail opens
4. ✅ You see a 3-level tree:
   ```
   Agent: Book flight  (AGENT, 2000ms)
   ├─ llm-call         (LLM, 900ms)
   └─ tool-use         (TOOL, 1000ms)
   ```
5. Click on **llm-call** span
6. ✅ Span detail shows: name, type, status=SUCCESS, latency, start/end time
7. Click on **tool-use** span
8. ✅ Same detail view works
9. Navigate back, try **"RAG: Customer support"** trace
10. ✅ Root span type is RETRIEVAL (not AGENT)

**API check:**
```bash
T=$(cat /tmp/token.txt)
curl -s "http://localhost:8000/tracer/trace/11111111-0001-0000-0000-000000000000/?project_id=aaaaaaaa-0001-0000-0000-000000000000" \
  -H "Authorization: Bearer $T" | python3 -c "
import sys,json; d=json.load(sys.stdin)
def show(n,i=0): s=n['observation_span']; print(' '*i+f\"- {s['name']} ({s['observation_type']})\"); [show(c,i+2) for c in n.get('children',[])]
[show(s) for s in d['result']['observation_spans']]"
# Expected: 3-level tree
```

---

## Flow 5.4 — Filters on Graph + Dropdown Metrics

**Goal:** Applying a filter changes the graph data. Switching the metric dropdown changes the chart.

### Steps
1. Open **Observe Test Project** → **Graph** tab
2. Open the **filter panel** (filter icon / "Add Filter")
3. Add filter: **Observation Type = LLM**
4. ✅ Graph updates — now shows only LLM spans (10 of the 30 spans)
5. Remove the filter
6. ✅ Graph returns to all spans
7. Add filter: **Status = SUCCESS**
8. ✅ Graph shows same data (all spans are SUCCESS)
9. Add filter: **Status = ERROR**
10. ✅ Graph shows 0 / empty (no error spans seeded — correct behaviour)
11. Clear filters
12. Use the **metric selector** and switch between **Latency / Tokens / Cost / Traffic**
13. ✅ Chart label + data changes for each metric

---

## Flow 6.1 — Cascade Delete Project

**Goal:** Deleting a project soft-deletes all its child traces and saved views.

> Use **Delete Me Project** for this test (has 2 traces, 6 spans, no eval config).

### Before you delete — note down:
```
Project: Delete Me Project (aaaaaaaa-0002-...)
Traces: 2
Spans in CH: 6
```

### Steps
1. Go to **Observe** → find **Delete Me Project**
2. Open project menu (3-dot / settings icon) → **Delete Project**
3. Confirm deletion
4. ✅ Project disappears from list

**Verify cascade in DB:**
```bash
docker exec postgres psql -U user -d tfc -c "
SELECT 'project' as type, count(*) FROM tracer_project WHERE id='aaaaaaaa-0002-0000-0000-000000000000'::uuid AND deleted=false
UNION ALL
SELECT 'traces', count(*) FROM tracer_trace WHERE project_id='aaaaaaaa-0002-0000-0000-000000000000'::uuid AND deleted=false;"
# Expected: both 0
```

---

## Flow 6.2 — Eval Logger Delete on Project Delete

**Goal:** Deleting a project also removes its `CustomEvalConfig` (eval loggers).

> Use **Observe Test Project** — it has eval config "Quality Eval" (`eeeeeeee-0001-...`).

### Before you delete — verify eval config exists:
```bash
docker exec postgres psql -U user -d tfc -c "SELECT id, name FROM tracer_custom_eval_config WHERE project_id='aaaaaaaa-0001-0000-0000-000000000000'::uuid AND deleted=false;"
# Expected: 1 row — "Quality Eval"
```

### Steps
1. Go to **Observe** → open **Observe Test Project**
2. Go to **Evals** tab — confirm "Quality Eval" appears
3. ✅ Eval config visible
4. Delete **Observe Test Project** (3-dot menu → Delete)
5. ✅ Project disappears

**Verify eval config cascade-deleted:**
```bash
docker exec postgres psql -U user -d tfc -c "
SELECT 'eval_configs' as type, count(*) FROM tracer_custom_eval_config WHERE project_id='aaaaaaaa-0001-0000-0000-000000000000'::uuid AND deleted=false
UNION ALL
SELECT 'traces', count(*) FROM tracer_trace WHERE project_id='aaaaaaaa-0001-0000-0000-000000000000'::uuid AND deleted=false;"
# Expected: both 0
```

---

## Re-seed Data

If you deleted the projects, re-seed by running:
```bash
! cd /Users/tanmayarora/future-agi && bash scripts/reseed_observe.sh
```

Or run each step manually — the full seeding commands are in the git history of this session.
