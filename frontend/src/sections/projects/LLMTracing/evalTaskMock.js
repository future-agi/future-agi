// ---------------------------------------------------------------------------
// Eval → Task resolution
// ---------------------------------------------------------------------------
//
// PRD "Group Evals by Eval Task" needs, for every Eval Metric column:
//   - the parent Task it belongs to (real name),
//   - the Task's run level (trace / span / session) — drives scoping (§4.3),
//   - the eval type (% / pass-fail / choice) — drives cell rendering (§4.4-4.6),
//   - a choice→colour mapping — drives chip colour (§4.6.2).
//
// Task identity + level come from the REAL eval-task API
// (/tracer/eval-task/list_eval_tasks/). LLMTracingView fetches it and calls
// `setEvalTaskRegistry(tasks)`; resolution then maps each eval column to its
// task by id/name. Coded defensively against field-name variation in the
// response. If a column can't be matched (or no task data loaded yet) it falls
// back to a single neutral "Evaluations" group — never a fabricated name.
//
// Eval type + choice tone are still derived locally (choice→colour config is a
// separate, not-yet-available backend feature per the PRD's Out of Scope).
// ---------------------------------------------------------------------------

export const TASK_LEVEL = { TRACE: "trace", SPAN: "span", SESSION: "session" };
export const EVAL_TYPE = {
  PERCENT: "percent",
  PASS_FAIL: "passfail",
  CHOICE: "choice",
};
export const CHOICE_TONE = { GOOD: "good", PARTIAL: "partial", BAD: "bad" };

const NEUTRAL_TASK = {
  taskId: "__evals__",
  taskName: "Evaluations",
  level: TASK_LEVEL.SPAN,
  createdAt: "1970-01-01",
};

// ── Prototype-only dummy task assignment ──────────────────────────────────
// The eval-task API does not yet expose the eval→task mapping (a backend
// change is planned — see setEvalTaskRegistry). Until it lands, give each
// distinct eval a STABLE dummy task so the task-grouped headers and source
// glyphs are exercisable on real trace data. Evals are chunked into tasks
// named "evaltaskname1", "evaltaskname2", … and the task level alternates
// (trace / span) so BOTH source glyphs ("T" / "S") show up. Swap-to-backend:
// once setEvalTaskRegistry() is fed real data, REGISTRY.populated becomes true
// and resolveEvalTask() never reaches this path.
const DUMMY_TASK_COUNT = 3;
const DUMMY_LEVELS = [TASK_LEVEL.TRACE, TASK_LEVEL.SPAN];

function dummyEvalKey(col) {
  return (
    norm(parentEvalFromName(col?.name)) ||
    norm(evalName(col)) ||
    norm(evalId(col)) ||
    "eval"
  );
}

// Stable string hash (djb2) — used to bucket evals into dummy tasks.
function dummyHash(s) {
  let h = 5381;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1) h = (h * 33) ^ str.charCodeAt(i);
  return Math.abs(h);
}

// A dummy task is a PURE function of the eval's key — the bucket is derived by
// hashing the key, never by insertion order or a running counter. This keeps
// each eval's task name stable when other evals are added/removed on a project
// (previously a new eval could shift everyone's bucket and rename tasks).
function dummyTaskFor(col) {
  const bucket = dummyHash(dummyEvalKey(col)) % DUMMY_TASK_COUNT;
  return {
    taskId: `__dummy_task_${bucket + 1}__`,
    taskName: `evaltaskname${bucket + 1}`,
    level: DUMMY_LEVELS[bucket % DUMMY_LEVELS.length],
    // Lower buckets sort first under groupEvalColumnsByTask's newest-first order.
    createdAt: new Date(Date.UTC(2030, 0, 1) - bucket * 86400000).toISOString(),
  };
}

const norm = (s) =>
  String(s == null ? "" : s)
    .trim()
    .toLowerCase();

function resolveLevel(rowType) {
  const t = norm(rowType);
  if (t.includes("session")) return TASK_LEVEL.SESSION;
  if (t.includes("trace")) return TASK_LEVEL.TRACE;
  if (t.includes("span")) return TASK_LEVEL.SPAN;
  return TASK_LEVEL.SPAN; // default: span (rolls up into trace view)
}

// Real registry, populated from the eval-task API. byKey maps every known eval
// identifier (id / config id / name, lowercased) to its task info.
let REGISTRY = { byKey: new Map(), populated: false };

/**
 * Populate the eval→task registry from the eval-task list API response.
 * Defensive about field names: tasks may expose evals as strings or objects,
 * under several possible keys.
 */
export function setEvalTaskRegistry(tasks) {
  const byKey = new Map();
  const list = Array.isArray(tasks) ? tasks : [];
  list.forEach((t, idx) => {
    if (!t || typeof t !== "object") return;
    const taskName =
      t.name ||
      t.task_name ||
      t.taskName ||
      t.eval_template_name ||
      `Task ${idx + 1}`;
    const taskId = String(t.id || t.task_id || t.taskId || taskName);
    const level = resolveLevel(t.row_type ?? t.rowType ?? t.level ?? t.type);
    const createdAt = t.created_at || t.createdAt || t.created || `1970-01-01`;
    const info = { taskId, taskName, level, createdAt };
    const evals =
      t.evals ||
      t.eval_configs ||
      t.evalConfigs ||
      t.metrics ||
      t.eval_metrics ||
      t.evaluations ||
      [];
    (Array.isArray(evals) ? evals : []).forEach((e) => {
      const keys = [];
      if (e == null) return;
      if (typeof e === "string") keys.push(e);
      else {
        keys.push(
          e.id,
          e.custom_eval_config_id,
          e.customEvalConfigId,
          e.eval_config_id,
          e.evalConfigId,
          e.name,
          e.eval_name,
          e.evalName,
          e.template_name,
          e.eval_template_name,
          e.metric_name,
        );
      }
      keys.filter(Boolean).forEach((k) => {
        if (!byKey.has(norm(k))) byKey.set(norm(k), info);
      });
    });
  });
  REGISTRY = { byKey, populated: byKey.size > 0 };
}

export const isEvalTaskRegistryPopulated = () => REGISTRY.populated;

const evalId = (col) =>
  col?.eval_config_id ||
  col?.custom_eval_config_id ||
  col?.id ||
  col?.name ||
  "";
const evalName = (col) => col?.name || col?.eval_name || col?.id || "";

// A per-choice eval column is named like "Avg. neutral (tone_26_Mar_2026)";
// the parent eval is in the trailing parentheses.
function parentEvalFromName(name) {
  const m = /\(([^)]+)\)\s*$/.exec(String(name || ""));
  return m ? m[1].trim() : null;
}

/**
 * Resolve the Task an eval column belongs to.
 * @returns {{ taskId, taskName, level, createdAt }}
 */
export function resolveEvalTask(col) {
  if (!col) return NEUTRAL_TASK;

  // 1. Real fields already on the column (future-proof / explicit override).
  if (col.eval_task_id || col.eval_task_name) {
    const taskId = String(col.eval_task_id || col.eval_task_name);
    return {
      taskId,
      taskName: col.eval_task_name || taskId,
      level: resolveLevel(col.task_level || col.row_type),
      createdAt: col.eval_task_created_at || "1970-01-01",
    };
  }

  // 2. Registry lookup by id / name / parent-eval-name (for per-choice cols).
  if (REGISTRY.populated) {
    const candidates = [
      evalId(col),
      evalName(col),
      parentEvalFromName(col.name),
    ].filter(Boolean);
    for (const c of candidates) {
      const hit = REGISTRY.byKey.get(norm(c));
      if (hit) return hit;
    }
  }

  // 3. No registry match yet → deterministic dummy task (prototype only, until
  //    the eval-task API exposes the mapping).
  return dummyTaskFor(col);
}

// Source-of-truth glyph meta for an eval's run level (PRD-v3 source glyph).
export function sourceMetaFromLevel(level) {
  if (level === TASK_LEVEL.TRACE)
    return { code: "T", label: "Trace-level eval" };
  if (level === TASK_LEVEL.SESSION)
    return { code: "Se", label: "Session-level eval" };
  return {
    code: "S",
    label: "Span-level eval — rolled up across this trace's spans",
  };
}

/** Glyph meta ("T" / "S") for the eval column's source level. */
export function getEvalSourceMeta(col) {
  return sourceMetaFromLevel(resolveEvalTask(col).level);
}

/** Eval type: percent | passfail | choice (§4.4-4.6). */
export function resolveEvalType(col) {
  const ot = norm(col?.output_type ?? col?.outputType);
  if (ot === "pass/fail" || ot === "pass_fail" || ot === "boolean")
    return EVAL_TYPE.PASS_FAIL;
  if (ot === "choices" || ot === "choice") return EVAL_TYPE.CHOICE;
  if (ot === "score" || ot === "numeric" || ot === "percentage")
    return EVAL_TYPE.PERCENT;
  return EVAL_TYPE.PERCENT;
}

// Choice tone classifier (§4.6.2). Real config (choice_color_map) wins; else a
// deterministic keyword classifier so chips read green/amber/red sensibly.
const GOOD_RE =
  /(accurate|^yes$|good|relevant|pass|correct|complete|grounded|safe|fully|positive)/i;
const PARTIAL_RE = /(partial|maybe|some|moderate|neutral|warn|mixed|surprise)/i;

export function classifyChoice(label, col) {
  const map = col?.choice_color_map || col?.choiceColorMap;
  if (map && map[label]) return map[label];
  const l = String(label || "");
  if (GOOD_RE.test(l)) return CHOICE_TONE.GOOD;
  if (PARTIAL_RE.test(l)) return CHOICE_TONE.PARTIAL;
  return CHOICE_TONE.BAD;
}

/**
 * Distinct Tasks present across a set of eval columns, ordered newest-first by
 * createdAt (§4.1.3). Each carries its ordered eval columns (= add order).
 */
export function groupEvalColumnsByTask(evalCols = []) {
  const groups = new Map();
  for (const col of evalCols) {
    const task = resolveEvalTask(col);
    let g = groups.get(task.taskId);
    if (!g) {
      g = { ...task, evals: [] };
      groups.set(task.taskId, g);
    }
    g.evals.push(col);
  }
  return Array.from(groups.values()).sort(
    (a, b) => new Date(b.createdAt) - new Date(a.createdAt),
  );
}

/** Eval columns in scope for a view (§4.3). view: 'trace' | 'span'. */
export function filterEvalColumnsForView(evalCols = [], view = "trace") {
  return evalCols.filter((col) => {
    const { level } = resolveEvalTask(col);
    if (view === "span") return level === TASK_LEVEL.SPAN;
    return level === TASK_LEVEL.TRACE || level === TASK_LEVEL.SPAN;
  });
}
