// Everything the Iterate dock shows, from the session store, R1 (configs +
// lineage), R3 matrix (scope counts, cost per attempt) and R3 grid (the
// current filter). The panes are presentational; this hook owns the rules.
import { toolsWithModes } from "./childCost";
import { useEffect, useMemo } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useMatrix, useRunConfigs } from "src/api/develop/r2";
import { useRunResults } from "src/api/develop/run-results";
import { modelCatalogQuery } from "src/api/model/model";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";
import { readResultsState } from "../results/useResultsUrlState";
import { useRootCauses } from "../matrix/useMatrixData";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { runLabel } from "src/sections/common/Runs/runLabel";
import { useIterateStore } from "./iterateStore";
import {
  MAX_DOCK_TRIALS,
  MAX_QUICK_TEST_ROWS,
  canIterate,
  changeTabsFor,
  collectChanges,
  costPerAttempt,
  focusRunOf,
  freeAgentVersion,
  landsAs,
  llmNodes,
  nextAgentVersion,
  nextPromptVersion,
  presetOf,
  previewOf,
  scopeOptions,
  scopeSets,
} from "./iterateModel";

const ENTRY_SCOPE = {
  all: "all",
  failed: "failed",
  then: "then",
  rows: "rows",
  filter: "filter",
  row: "row",
};

/**
 * options-truth L2 G9: the change a door names (`change=`), drafted so the
 * dock opens ready to run it. `model:<id>` sets the model (Model & params,
 * or the harness's model); a preset (`RC_PREFIX`, `RC_PREFIX:keep`) fills
 * its node's prompt with the preset's chip. Pure; `{}` when the run has no
 * place for the change (an agent graph has no model tab).
 */
export function changeOfEntry(
  change,
  { tabs = [], preset = null, node, nodes = [] } = {},
) {
  const raw = String(change ?? "").trim();
  if (!raw) return {};
  const [axis, ...rest] = raw.split(":");
  const value = rest.join(":").trim();
  if (axis === "model" && value) {
    if (tabs.includes("model")) return { tab: "model", model: { id: value } };
    if (tabs.includes("harness"))
      return { tab: "harness", harness: { model: value } };
    return {};
  }
  if (preset?.chips?.length && preset.node) {
    const want = value.toLowerCase();
    const chip =
      preset.chips.find(
        (x) =>
          want && (x.id === want || x.label.toLowerCase().startsWith(want)),
      ) ?? preset.chips.find((x) => x.id === preset.chip);
    const target = nodes.find((n) => n.id === preset.node)?.id ?? node;
    if (chip && target && tabs.includes(preset.tab))
      return { tab: preset.tab, prompts: { [target]: chip.text } };
  }
  return {};
}

/** The first draft for a parent run (and the entry that opened the dock). */
export function initialDraft(parent, entry) {
  const c = parent?.config ?? {};
  const preset = presetOf(entry?.change, entry?.cause);
  const tabs = changeTabsFor(parent);
  const nodes = llmNodes(c);
  const node =
    (preset?.node && nodes.find((n) => n.id === preset.node)?.id) ??
    (entry?.node && nodes.find((n) => n.id === entry.node)?.id) ??
    nodes[0]?.id ??
    null;
  // R4 implementation (8e): Test a fix starts on the rows that failed (opened from
  // a failed row or lane, or with no scope named); `all` only when asked.
  const scope = ENTRY_SCOPE[entry?.scope] ?? "failed";
  // iterate r2 G3 (P3): the door's rows are a scope only when the door
  // names them (`scope=rows` / `row`); a `Failed` door never adds a stray
  // `1 selected row` chip.
  const doorRows = scope === "rows" || scope === "row" ? entry?.rows ?? [] : [];
  const named = changeOfEntry(entry?.change, { tabs, preset, node, nodes });
  return {
    tab:
      named.tab ??
      (preset?.tab && tabs.includes(preset.tab) ? preset.tab : tabs[0]),
    node,
    prompts: named.prompts ?? {},
    maxIterations: c.agent?.limits?.max_iterations ?? null,
    messages: null,
    model: named.model ?? null,
    tools: null,
    harness: named.harness ?? null,
    scope,
    rows: doorRows,
    // A single-row door (drawer) offers `This row`, not `These 1 rows`.
    rowOnly: scope === "row",
    trials: Math.min(MAX_DOCK_TRIALS, c.trials ?? parent?.trials ?? 1),
    carryOver: true,
    quickTest: scope === "row",
    presetId: preset?.id ?? null,
    causeId: entry?.cause ?? null,
    chip: null,
    saveName: null,
    nonce: entry?.nonce ?? null,
    // iterate r1 G3 (P1): `Fix this path` names the path and the reason.
    reason: entry?.reason ?? null,
    pathId: entry?.pathId ?? null,
    // iterate r2 G3 (P3): `← Back to the trace` (the door's page).
    back: entry?.back ?? null,
  };
}

/** `order_found` from a finding's failed eval (`check:order_found`, name). */
export function causeCheckLabel(cause) {
  const raw = cause?.failed_eval ?? cause?.failed_eval_name ?? null;
  if (!raw) return null;
  return String(raw).replace(/^(check|eval):/, "");
}

/**
 * The finding a dock entry names (`cause=` id), from R5 root causes: the
 * one on the dock's run first. Pure.
 */
export function causeOf(causes, causeId, runId) {
  if (!causeId) return null;
  const list = (causes ?? []).filter((c) => c?.id === causeId);
  return list.find((c) => c.run_id === runId) ?? list[0] ?? null;
}

/**
 * iterate r1 G3 (P3): what a new door keeps from the run's draft — the
 * edits (and what last ran), never the scope / reason of the old door.
 */
export const KEPT_EDITS = Object.freeze([
  "prompts",
  "messages",
  "model",
  "tools",
  "harness",
  "maxIterations",
  "saveName",
  "ran",
]);

/** The draft a door opens on: a fresh one with the previous edits kept. */
export function reopenDraft(parent, entry, previous) {
  const next = initialDraft(parent, entry);
  KEPT_EDITS.forEach((k) => {
    const v = previous?.[k];
    if (v == null) return;
    if (k === "prompts" && !Object.keys(v).length) return;
    next[k] = v;
  });
  return next;
}

/** A comparable snapshot of a draft's edits (`ran.edits`). */
export const editsKey = (parent, d) => JSON.stringify(draftEdits(parent, d));

/** The draft's change edits as collectChanges input. */
export function draftEdits(parent, d) {
  const c = parent?.config ?? {};
  const node = (c.agent?.nodes ?? []).find((n) => n.id === d.node);
  return {
    agent: c.agent
      ? {
          node: d.node,
          prompt: d.prompts?.[d.node] ?? node?.prompt,
          maxIterations: d.maxIterations ?? c.agent?.limits?.max_iterations,
        }
      : undefined,
    prompt: d.messages ? { messages: d.messages } : undefined,
    model: d.model ?? undefined,
    tools: d.tools ?? undefined,
    harness: d.harness ?? undefined,
  };
}

export default function useIterateDock({ datasetId, runIds, baselineId }) {
  const { search } = useLocation();
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const storeRunId = useIterateStore((s) => s.runId);
  const opened = useIterateStore((s) => s.opened);
  const entry = useIterateStore((s) => s.entry);
  const drafts = useIterateStore((s) => s.drafts);
  const patchDraft = useIterateStore((s) => s.patchDraft);

  const configs = useRunConfigs(datasetId, { enabled: opened });
  const runs = useMemo(() => configs.data?.results ?? [], [configs.data]);
  const cand = readR2Params(search).cand;
  // dataset integration FE-0: the dock opens only on a run Test a fix applies to (a
  // prompt or agent run); a code, harness, voice or uploaded run has none.
  const inDataset = (id) =>
    id && runs.some((r) => r.run_id === id && canIterate(r));
  // v2.1 g4 (door:test-a-fix:*): a page whose runs Test a fix does not
  // apply to (code or harness runs only) focuses none of them, never a run
  // of another experiment of the dataset.
  const pageIds = (runIds ?? []).filter(Boolean);
  const iterableIds = pageIds.filter(inDataset);
  const focused =
    pageIds.length && !iterableIds.length
      ? null
      : focusRunOf({
          runIds: iterableIds,
          runs,
          baselineId,
          cand,
          urlBaseline: new URLSearchParams(search).get("baseline"),
        });
  const parentId = inDataset(storeRunId)
    ? storeRunId
    : inDataset(focused)
      ? focused
      : null;
  const parent = runs.find((r) => r.run_id === parentId) ?? null;
  const baseline =
    baselineId && baselineId !== parentId
      ? runs.find((r) => r.run_id === baselineId) ?? null
      : null;

  // A new entry (URL or button) restarts the draft for its run.
  useEffect(() => {
    // An entry without a run (Key I) opens on the run in focus.
    if (!parent || !entry || (entry.runId && entry.runId !== parent.run_id))
      return;
    if (drafts[parent.run_id]?.nonce === entry.nonce) return;
    patchDraft(parent.run_id, (prev) => reopenDraft(parent, entry, prev));
    useIterateStore.getState().consumeEntry();
  }, [parent, entry, drafts, patchDraft]);

  const draft = useMemo(() => {
    if (!parent) return null;
    return { ...initialDraft(parent, null), ...(drafts[parent.run_id] ?? {}) };
  }, [parent, drafts]);

  const matrixRuns = baseline ? [baseline.run_id, parentId] : [parentId];
  const matrix = useMatrix(
    { datasetId, runIds: parentId ? matrixRuns : [], baselineId: parentId },
    { enabled: opened && Boolean(parentId) },
  );

  // The grid's current filter (results pills / show) as row ids.
  const resultsState = readResultsState(new URLSearchParams(search));
  const hasFilter =
    (resultsState.pills?.length ?? 0) > 0 ||
    (resultsState.show && resultsState.show !== "all");
  const filtered = useRunResults(
    datasetId,
    {
      runIds: runIds?.length ? runIds : parentId ? [parentId] : [],
      baselineId,
      pills: resultsState.pills,
      show: resultsState.show,
    },
    { enabled: opened && hasFilter && Boolean(parentId) },
  );
  const filteredRows = filtered.data?.rows;
  const filterRowIds = useMemo(
    () => (hasFilter ? (filteredRows ?? []).map((r) => r.example_id) : null),
    [hasFilter, filteredRows],
  );

  // P3: a finding's rows and check name (`Rows failing order_found (6)`).
  const causeId = draft?.causeId ?? null;
  const rootCauses = useRootCauses(
    { datasetId, runIds, baselineId },
    { enabled: opened && Boolean(causeId) },
  );
  const cause = useMemo(
    () => causeOf(rootCauses.data?.causes, causeId, parentId),
    [rootCauses.data, causeId, parentId],
  );

  const kind = parent ? runKindOf(parent) : "prompt";
  const models = useQuery({
    ...modelCatalogQuery(""),
    enabled: opened && kind === "prompt",
  });

  return useMemo(() => {
    if (!parent || !draft)
      return {
        ready: false,
        loading: configs.isLoading,
        runs,
        canEdit,
        // v2.1 g4: this page's runs are none Test a fix applies to.
        noIterable:
          !configs.isLoading &&
          runs.length > 0 &&
          pageIds.length > 0 &&
          !iterableIds.length,
      };
    const label = runLabel(parent);
    const preset = draft.presetId ? presetOf(draft.presetId) : null;
    const causeRows = (cause?.rows ?? []).map((r) => r.row_id).filter(Boolean);
    const causeLabel = causeCheckLabel(cause);
    // A `scope=rows` door with a finding but no rows: the finding's rows.
    const doorRows =
      draft.scope === "rows" && !draft.rows?.length && causeRows.length
        ? causeRows
        : draft.rows;
    const sets = scopeSets(matrix.data, parentId, {
      baselineId: baseline?.run_id,
      rows: draft.rowOnly ? [] : doorRows,
      filterRowIds,
      rowId: draft.rowOnly ? draft.rows?.[0] ?? null : null,
    });
    const options = scopeOptions(sets, {
      parentLabel: label,
      baselineLabel: baseline ? runLabel(baseline) : null,
      source: draft.causeId ? "Finding" : "Selection",
      causeLabel: draft.causeId ? causeLabel : null,
    });
    // IT-R5 G3 (P4): `Rerun: Failed (13) ▾` + `Also verify the other 17
    // rows`. The old `then` scope is the Failed scope with that box ticked.
    const asked = draft.scope === "then" ? "failed" : draft.scope;
    const scopeType = options.some(
      (o) =>
        o.type === asked &&
        o.type !== "then" &&
        // No failed rows: fall back to every row.
        !(o.type === "failed" && !o.count),
    )
      ? asked
      : "all";
    const scopeRows = sets[scopeType] ?? [];
    const scopeSet = new Set(scopeRows);
    const others = (sets.all ?? []).filter((id) => !scopeSet.has(id));
    const alsoVerify = draft.scope === "then" || Boolean(draft.alsoVerify);
    // P4: the scope's rows run now and the rest right after (one verdict).
    const then =
      alsoVerify && !draft.quickTest && scopeRows.length && others.length
        ? { first: [...scopeRows], rest: others }
        : null;
    const rowIds = scopeRows;
    const labelById = new Map(
      (matrix.data?.rows ?? []).map((r) => [r.row_id, r.label ?? `Row ${r.n}`]),
    );
    const rowLabels = rowIds.map((id) => labelById.get(id) ?? id);
    const quickTest = Boolean(draft.quickTest);
    const trials = quickTest ? 1 : draft.trials;
    const changes = collectChanges(parent.config, draftEdits(parent, draft));
    // P3: the draft as it last ran (nothing new to run until it changes).
    const sameAsRan = Boolean(
      draft.ran &&
        changes.length &&
        draft.ran.edits === editsKey(parent, draft),
    );
    const costEach = costPerAttempt(matrix.data, parentId);
    const edits = draftEdits(parent, draft);
    const fromModel = parent.config?.harness?.model ?? parent.config?.model?.id;
    const toModel = edits.harness?.model ?? edits.model?.id ?? fromModel;
    // L3 G9 (fix 9): the child's tool modes and cost cap price its tries.
    // v2.1 g4: and its max tokens (the engine caps each reply there).
    const fromMaxTokens =
      parent.config?.model?.params?.max_tokens ??
      parent.config?.model?.params?.maxTokens ??
      null;
    const costKnobs = {
      fromMaxTokens,
      toMaxTokens: edits.model?.maxTokens ?? fromMaxTokens,
      fromTools: parent.config?.tools ?? [],
      toTools: toolsWithModes(parent.config?.tools ?? [], edits.tools?.modes),
      capUsd:
        edits.harness?.maxCostUsd ??
        parent.config?.harness?.max_cost_usd ??
        parent.config?.harness?.budget?.max_cost_usd ??
        null,
    };
    const preview = previewOf({
      rows: then ? sets.all.length : rowIds.length,
      trials,
      kind,
      costPerAttemptUsd: costEach,
      key: parentId,
      fromModel,
      toModel,
      ...costKnobs,
    });
    const lands = landsAs({
      parent,
      datasetRuns: runs,
      quickTest,
      parentLabel: label,
    });
    const carriedCount = then ? 0 : sets.all.length - rowIds.length;
    // P4: what `Also verify the other 17 rows` adds (`+$0.39`).
    const verifyPreview = previewOf({
      rows: others.length,
      trials,
      kind,
      costPerAttemptUsd: costEach,
      key: parentId,
      fromModel,
      toModel,
      ...costKnobs,
    });
    const agentVersion = parent.config?.agent?.version;
    // v2.1 g4: the default agent version name is the next one no run's
    // version holds (the server moves a taken name to the next free one).
    const saveName =
      draft.saveName ??
      (parent.config?.agent
        ? freeAgentVersion(
            nextAgentVersion(agentVersion),
            runs.map((r) => r.config?.agent?.version),
          )
        : nextPromptVersion(parent.config?.prompt?.version));
    const blockers = [];
    const counting = matrix.isLoading || !matrix.data;
    if (!canEdit) blockers.push("Editors can start runs");
    if (counting) blockers.push("Counting rows…");
    else if (!rowIds.length) blockers.push("No rows match this scope");
    if (quickTest && rowIds.length > MAX_QUICK_TEST_ROWS)
      blockers.push(`A quick test runs at most ${MAX_QUICK_TEST_ROWS} rows`);
    return {
      ready: true,
      canEdit,
      runs,
      parent,
      parentId,
      label,
      baseline,
      kind,
      draft,
      preset,
      // P2/P3: what the fix aims at (the banner's target rows).
      cause: cause
        ? { id: cause.id, label: causeLabel, rows: causeRows }
        : null,
      back: draft.back ?? null,
      tabs: changeTabsFor(parent),
      nodes: llmNodes(parent.config),
      sets,
      options,
      scopeType,
      rowIds,
      then,
      others,
      verifyPreview,
      rowLabels,
      carriedCount,
      quickTest,
      trials,
      changes,
      sameAsRan,
      preview,
      lands,
      saveName,
      blockers,
      matrixLoading: counting,
      models: models.data?.results ?? [],
      patch: (p) => patchDraft(parentId, p),
    };
  }, [
    parent,
    draft,
    configs.isLoading,
    runs,
    canEdit,
    matrix.data,
    matrix.isLoading,
    parentId,
    baseline,
    filterRowIds,
    kind,
    models.data,
    patchDraft,
    cause,
    pageIds.length,
    iterableIds.length,
  ]);
}
