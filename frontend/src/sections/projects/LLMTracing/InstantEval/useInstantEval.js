import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  FIELD_TYPE_ALIASES,
  LIST_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";
import {
  ANSWER_TYPES,
  INSTANT_EVAL_MODEL,
  YES_NO_LABELS,
  answerLabels,
  buildEvalMapping,
  buildTemplatePayload,
  cleanDefinitions,
} from "./instantEvalPayloads";
import { normalizeReads, splitScopeFilters } from "./instantEvalUnits";
import { ERROR_ANSWER } from "./instantEvalAnswers";

export { ERROR_ANSWER };

const result = (response) => response?.data?.result;

export function buildAnswerFilter(item, labels) {
  return {
    column_id: item.configId,
    property_id: `eval_config:${item.configId}`,
    display_name: item.question,
    filter_config: {
      filter_type: "text",
      filter_op: "in",
      filter_value: labels,
      col_type: "EVAL_METRIC",
    },
  };
}

export function itemFromConfig(config) {
  const meta = config?.config?.instant_eval;
  if (!meta?.question) return null;
  return {
    configId: config.id,
    name: config.name,
    templateId: config.eval_template,
    question: meta.question,
    answerType: meta.answer_type,
    labels: meta.labels || [],
    definitions: meta.definitions || {},
    reads: meta.reads,
    // Questions saved before sessions and users had Instant eval were all
    // asked about traces.
    unit: meta.unit || "traces",
  };
}

const configName = (question, taken) => {
  const base = question.trim().replace(/\s+/g, " ").slice(0, 200);
  let name = base;
  let n = 2;
  while (taken.has(name.toLowerCase())) name = `${base} (${n++})`;
  taken.add(name.toLowerCase());
  return name;
};

const sameLabels = (a = [], b = []) =>
  a.length === b.length && a.every((label, index) => label === b[index]);

const sameDefinitions = (a = {}, b = {}) => {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => (a[key] || "") === (b[key] || ""));
};

// A saved question is reused only when it asks the same thing of the same
// kind of row: same wording, answers, definitions, inputs and unit.
export const matchesSavedQuestion = (
  item,
  { text, answerType, labels, definitions, reads, unit },
) =>
  item.question.toLowerCase() === text.toLowerCase() &&
  item.answerType === answerType &&
  item.reads === reads &&
  (item.unit || "traces") === unit &&
  sameLabels(item.labels, labels) &&
  sameDefinitions(item.definitions, definitions);

async function listProjectConfigs(projectId) {
  const response = await axios.get(endpoints.project.getEvalTaskConfig(), {
    params: { project_id: projectId },
  });
  return result(response) || [];
}

// Asking a question that already has an evaluator in this project (same
// wording, answers, inputs and unit) reuses it, so its grid column and history
// stay one series. Only new questions create a Jev template and an eval config.
export async function resolveEvaluators({ projectId, questions, reads, unit }) {
  // The list comes back newest first, so the first match is the newest
  // evaluator for a question.
  const configs = await listProjectConfigs(projectId);
  const existing = configs.map(itemFromConfig).filter(Boolean);
  const taken = new Set(
    configs.map((config) => (config.name || "").toLowerCase()),
  );
  const mapping = buildEvalMapping(reads);
  const now = new Date();
  return Promise.all(
    questions.map(async (question, index) => {
      const text = question.question.trim();
      const labels = answerLabels(question);
      const definitions = cleanDefinitions(question.definitions, labels);
      const reuse = existing.find((item) =>
        matchesSavedQuestion(item, {
          text,
          answerType: question.answerType,
          labels,
          definitions,
          reads,
          unit,
        }),
      );
      if (reuse) return reuse;
      const template = await axios.post(
        endpoints.develop.eval.createEvalTemplateV2,
        buildTemplatePayload({
          ...question,
          reads,
          unit,
          now: new Date(now.getTime() + index * 1000),
        }),
      );
      const templateId = result(template)?.id;
      const name = configName(text, taken);
      const config = await axios.post(
        endpoints.project.createEvalTaskConfig(),
        {
          project: projectId,
          eval_template: templateId,
          name,
          model: INSTANT_EVAL_MODEL,
          mapping,
          error_localizer: false,
          config: {
            mapping,
            run_config: { model: INSTANT_EVAL_MODEL },
            instant_eval: {
              question: text,
              answer_type: question.answerType,
              labels,
              ...(Object.keys(definitions).length ? { definitions } : {}),
              reads,
              unit,
            },
          },
        },
      );
      return {
        configId: result(config)?.id,
        templateId,
        name,
        question: text,
        answerType: question.answerType,
        labels,
        definitions,
        reads,
        unit,
      };
    }),
  );
}

/**
 * The server's answer to "how many rows does this view hold, and what would
 * reading a sample or all of it cost": one count with the grid's own filters.
 */
export async function estimateAsk({
  projectId,
  unit,
  filters = [],
  ids,
  search,
  datePreset,
  label,
  sampleSize = null,
  questions = 1,
  signal,
}) {
  const response = await axios.post(
    endpoints.project.instantEvalEstimate(),
    {
      project_id: projectId,
      unit,
      filters,
      ...(ids?.length ? { ids } : {}),
      ...(search ? { search } : {}),
      ...(datePreset ? { date_preset: datePreset } : {}),
      ...(label ? { label: String(label).slice(0, 300) } : {}),
      sample_size: sampleSize,
      questions,
    },
    { signal },
  );
  return result(response);
}

/** A start the server holds back until the person confirms the credits. */
export class AskNeedsConfirm extends Error {
  constructor(detail) {
    super(detail?.message || "Confirm to start.");
    this.name = "AskNeedsConfirm";
    this.rows = detail?.rows_to_read ?? null;
    this.credits = detail?.credits_estimate ?? null;
  }
}

/** A start refused because the organization's runs are all in use. */
export class AskBusy extends Error {
  constructor(detail) {
    super(detail?.message || "Too many Instant eval runs are in progress.");
    this.name = "AskBusy";
    this.runs = detail?.runs || [];
  }
}

/**
 * A start refused by billing: not enough credits left this month, a paused
 * budget or an unpaid invoice. `sampleSize` is the largest sample that fits.
 */
export class AskNoCredits extends Error {
  constructor(detail) {
    super(detail?.message || "Your plan has no credits left for this run.");
    this.name = "AskNoCredits";
    this.code = detail?.error_code || null;
    this.sampleSize = detail?.sample_size ?? null;
  }
}

/**
 * A start refused because its sample is larger than the view, or than one
 * run reads. `sampleSize` is the largest that can run.
 */
export class AskSampleTooLarge extends Error {
  constructor(detail) {
    super(
      detail?.message ||
        detail?.detail ||
        "That sample is larger than this view allows.",
    );
    this.name = "AskSampleTooLarge";
    this.sampleSize = detail?.sample_size ?? null;
  }
}

// Controls the server refuses with a reason worded for the person: a
// cancelled run is asked again, not resumed; a run still answering can't
// read more yet; a paused run with rows left is resumed before a retry.
const CONTROL_REFUSALS = new Set([
  "INSTANT_EVAL_CANCELLED",
  "INSTANT_EVAL_STILL_ANSWERING",
  "INSTANT_EVAL_RUNNING",
  "INSTANT_EVAL_PAUSED",
]);

export class AskRefused extends Error {
  constructor(detail) {
    super(detail?.message || "That can't be done on this run.");
    this.name = "AskRefused";
    this.code = detail?.error_code || detail?.code || null;
  }
}

// Jev runs on the organization's own TypeSafe key; key problems point to the
// AI Providers settings where it is added or replaced.
export const AI_PROVIDERS_SETTINGS_PATH = "/dashboard/settings/ai-providers";
export const JEV_KEY_NEEDED =
  "Jev runs on your own TypeSafe key. Add it in Settings → AI Providers to start.";
const JEV_KEY_MISSING = "JEV_KEY_MISSING";

/**
 * A start, read more, resume or retry refused because the questions run on
 * Jev and the organization has no TypeSafe key saved: every row would fail
 * on it. The server names the same settings page the app links to.
 */
export class AskKeyMissing extends Error {
  constructor(detail) {
    super(detail?.message || JEV_KEY_NEEDED);
    this.name = "AskKeyMissing";
    this.code = JEV_KEY_MISSING;
    this.settingsPath = AI_PROVIDERS_SETTINGS_PATH;
  }
}

// The app's axios rejects with the response's error envelope and its
// `statusCode`; a raw Axios error carries them under `response`.
const errorStatus = (error) => error?.response?.status ?? error?.statusCode;

const errorDetail = (error) => {
  const data = error?.response?.data || error || {};
  return typeof data.result === "object" && data.result ? data.result : data;
};

/** Turn a control or start refusal the page explains into its own error. */
export function askError(error) {
  const status = errorStatus(error);
  const detail = errorDetail(error);
  if (status === 409 && detail.error_code === "INSTANT_EVAL_CONFIRM")
    return new AskNeedsConfirm(detail);
  if ((status === 402 || status === 403) && detail.credits_estimate != null)
    return new AskNoCredits(detail);
  if (
    status === 400 &&
    [detail.error_code, detail.code, error?.code].includes(JEV_KEY_MISSING)
  )
    return new AskKeyMissing(detail);
  if (
    status === 400 &&
    (detail.code === "instant_eval_sample_too_large" ||
      detail.error_code === "INSTANT_EVAL_SAMPLE_TOO_LARGE")
  )
    return new AskSampleTooLarge(detail);
  if (
    status === 409 &&
    CONTROL_REFUSALS.has(
      String(detail.error_code || detail.code || "").toUpperCase(),
    )
  )
    return new AskRefused(detail);
  // A Users count still under way: what was counted is kept, so starting
  // again carries on.
  if (status === 503 && (detail.code || error?.code) === "users_still_counting")
    return new AskRefused(detail);
  if (status === 429) return new AskBusy(detail);
  return error;
}

/**
 * Ask Jev the questions about a view: save each question as a Jev evaluator
 * (or reuse the saved one), then let the server resolve the view's rows (a
 * hash-ordered sample of `sampleSize`, or all of them) or read the rows
 * handed over by id, and start the run. The view's date option and label
 * are kept with the run, so it can be asked again over a window moved to
 * now.
 */
export async function startAskRun({
  projectId,
  unit,
  questions,
  existingItems,
  reads,
  filters = [],
  ids,
  search,
  sampleSize = null,
  confirm = false,
  confirmedRows,
  datePreset,
  label,
}) {
  const runReads = normalizeReads(unit, reads || existingItems?.[0]?.reads);
  const saved = existingItems
    ? existingItems
    : await resolveEvaluators({ projectId, questions, reads: runReads, unit });
  if (saved.some((item) => !item.configId))
    throw new Error("The question could not be saved as an evaluator.");
  let response;
  try {
    response = await axios.post(endpoints.project.instantEvalStart(), {
      project_id: projectId,
      unit,
      // Users handed over by id are read inside the view's window.
      filters: ids?.length && unit !== "users" ? [] : filters,
      ...(ids?.length ? { ids } : {}),
      ...(search && !ids?.length ? { search } : {}),
      sample_size: ids?.length ? null : sampleSize,
      config_ids: saved.map((item) => item.configId),
      confirm,
      // What the person confirmed: the server asks again if it would read
      // more (a Users count still climbing, a window grown since).
      ...(confirm && Number.isFinite(confirmedRows)
        ? { confirmed_rows: confirmedRows }
        : {}),
      ...(datePreset ? { date_preset: datePreset } : {}),
      ...(label ? { label: String(label).slice(0, 300) } : {}),
    });
  } catch (error) {
    const known = askError(error);
    if (known !== error) known.questions = saved;
    throw known;
  }
  const started = result(response);
  if (!started?.task_id) throw new Error("The run could not be started.");
  return {
    taskId: started.task_id,
    questions: saved,
    rows: started.rows_requested,
    population: started.population,
    isSample: Boolean(started.is_sample),
  };
}

const LIVE_TASK_PREFIX = "Instant eval (live)";
const LIVE_TASK_PAGE_SIZE = 50;
const LIVE_TASK_MAX_PAGES = 20;

// Same evaluators, same view filters, no date window: answers every new trace
// (or span) that matches this view as it arrives.
export async function createContinuousTask({
  projectId,
  items,
  scopeFilters,
  unit = "traces",
}) {
  const { filters } = splitScopeFilters(scopeFilters);
  const response = await axios.post(endpoints.project.createEvalTask(), {
    name: `${LIVE_TASK_PREFIX} · ${items[0].question}`.slice(0, 255),
    project: projectId,
    evals: items.map((item) => item.configId),
    filters: filters.length ? { filters } : {},
    run_type: "continuous",
    row_type: unit === "spans" ? "spans" : "traces",
    sampling_rate: 100,
  });
  return result(response)?.id;
}

/**
 * Whether a live task already answers these questions couldn't be told, so
 * none was started: starting one blind could answer every new trace twice.
 */
export class LiveTaskUnchecked extends Error {
  constructor(
    message = "Couldn't check whether these questions are already answered live, so nothing was started. Try again in a moment.",
  ) {
    super(message);
    this.name = "LiveTaskUnchecked";
  }
}

// The task list names a task's evaluators by config name, which is unique in
// a project. The names are read fresh by config id, so a question shown
// without its name, or renamed since, still finds its task.
async function liveTaskNames(projectId, items) {
  const ids = items.map((item) => item.configId);
  if (!ids.length || ids.some((id) => !id)) throw new LiveTaskUnchecked();
  const configs = await listProjectConfigs(projectId);
  const byId = new Map(configs.map((config) => [String(config.id), config]));
  const names = ids.map((id) => byId.get(String(id))?.name);
  if (names.some((name) => !name))
    throw new LiveTaskUnchecked(
      "One of these questions' evaluators is gone, so it can't be answered live.",
    );
  return new Set(names);
}

const CANONICAL_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const isPlainObject = (value) =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

// A filter as the server saves it (FilterItemField and
// bind_my_annotations_principal): its type lowercased and de-aliased, a JSON
// span attribute matched against an object read as a map, comma-joined ids
// pasted into a text list split into one member per id, and "My annotations"
// bound to the user who saved it.
function savedFilter(item, userId) {
  const config = item?.filter_config;
  if (!isPlainObject(config)) return item;
  const raw = String(config.filter_type ?? "").toLowerCase();
  const type =
    config.col_type === "SPAN_ATTRIBUTE" &&
    raw === "json" &&
    isPlainObject(config.filter_value)
      ? "map"
      : FIELD_TYPE_ALIASES[raw] || raw;
  const saved = { ...config, filter_type: type };
  if (
    config.attribute_value_types == null &&
    LIST_FILTER_OPS.includes(config.filter_op) &&
    Array.isArray(config.filter_value) &&
    type === "text"
  ) {
    saved.filter_value = config.filter_value.flatMap((member) => {
      if (typeof member !== "string" || !member.includes(",")) return [member];
      const ids = member
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
      return ids.length && ids.every((id) => CANONICAL_UUID.test(id))
        ? ids
        : [member];
    });
  }
  if (item.column_id === "my_annotations") saved.user_id = String(userId);
  return { ...item, filter_config: saved };
}

// A view's filters as one comparable key: in any order, with keys sorted.
// `userId` binds the browser's filters as the server would for this user; a
// saved task's are already bound to whoever saved it.
function filtersKey(filters = [], userId) {
  const canonical = (value) => {
    if (Array.isArray(value)) return value.map(canonical);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  };
  return JSON.stringify(
    (Array.isArray(filters) ? filters : [])
      .map((item) =>
        JSON.stringify(
          canonical(userId === undefined ? item : savedFilter(item, userId)),
        ),
      )
      .sort(),
  );
}

// One live task per set of questions and view: re-creating it would answer
// every new trace twice and bill twice. The same questions over another
// view's filters are another task.
// Live tasks are found by name, page by page, so a long run history can't hide
// an existing one. A list too long to read to its end refuses rather than
// risk a duplicate.
export async function findContinuousTask({
  projectId,
  items,
  scopeFilters,
  unit = "traces",
  userId,
}) {
  const wanted = await liveTaskNames(projectId, items);
  const { filters } = splitScopeFilters(scopeFilters || []);
  // "My annotations" means this user's: without knowing who that is, a task
  // over it can't be matched.
  if (
    userId == null &&
    filters.some((item) => item?.column_id === "my_annotations")
  )
    throw new LiveTaskUnchecked();
  const view = filtersKey(filters, userId ?? null);
  const rowType = unit === "spans" ? "spans" : "traces";
  const matches = (task) =>
    task.run_type === "continuous" &&
    task.row_type === rowType &&
    !["deleted", "failed"].includes(task.status) &&
    (task.evals_applied || []).length === wanted.size &&
    (task.evals_applied || []).every((name) => wanted.has(name)) &&
    filtersKey(task.filters_applied?.filters) === view;
  for (let page = 0; page < LIVE_TASK_MAX_PAGES; page += 1) {
    // eslint-disable-next-line no-await-in-loop
    const response = await axios.get(endpoints.project.getEvalTaskList(), {
      params: {
        project_id: projectId,
        name: LIVE_TASK_PREFIX,
        page_number: page,
        page_size: LIVE_TASK_PAGE_SIZE,
      },
    });
    const rows = result(response)?.table || [];
    const found = rows.find(matches);
    if (found) return found;
    if (rows.length < LIVE_TASK_PAGE_SIZE) return undefined;
  }
  throw new LiveTaskUnchecked();
}

// The saved questions' query, read again once a start has saved new ones.
export const SAVED_QUESTIONS_KEY = "instant-evals";

/**
 * Questions saved by Instant eval in this project. `items` has one entry per
 * question (the newest evaluator for it), optionally only those asked about
 * one unit; `all` keeps every evaluator for matching runs back to questions.
 */
export function useInstantEvals(projectId, { enabled = true, unit } = {}) {
  const query = useQuery({
    queryKey: [SAVED_QUESTIONS_KEY, projectId],
    enabled: Boolean(projectId) && enabled,
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskConfig(), {
        params: { project_id: projectId },
      }),
    select: (response) => {
      // Newest first, as the list comes back (CustomEvalConfig orders by
      // -created_at): "Recent questions" lead with the latest.
      const configs = result(response) || [];
      return configs.map(itemFromConfig).filter(Boolean);
    },
    staleTime: 30_000,
  });
  const all = query.data || [];
  const seen = new Set();
  // One entry per question: re-asking a question creates a new evaluator,
  // and the newest one is the one worth re-applying.
  const items = all.filter((item) => {
    if (unit && item.unit !== unit) return false;
    const key = item.question.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return {
    all,
    items,
    isLoading: query.isLoading,
    refetch: query.refetch,
  };
}

const HISTORY_PAGE_SIZE = 20;
// A history page is read again this often while one of its runs is still
// being answered.
const HISTORY_LIVE_MS = 5000;
export const HISTORY_KEY = "instant-eval-history";
const LIVE_RUN_STATUSES = new Set(["pending", "running"]);

// Runs that ended (or stopped before finishing) aren't polled for, even with
// rows unanswered.
const ENDED_RUN_STATUSES = new Set([
  "completed",
  "failed",
  "paused",
  "cancelled",
  "stalled",
]);

export const isLiveRun = (runItem) =>
  LIVE_RUN_STATUSES.has(runItem.status) ||
  (runItem.pending > 0 && !ENDED_RUN_STATUSES.has(runItem.status));

// Live tasks ("Keep answering") share the runs' name but never finish;
// History lists runs only.
const isHistorical = (run) => (run.run_type || "historical") === "historical";

/** How often History's pages are read again: while a run on them is live. */
export const historyRefetchMs = (pages = []) =>
  pages.some((page) =>
    page.runs.some((run) => isHistorical(run) && isLiveRun(run)),
  )
    ? HISTORY_LIVE_MS
    : false;

// A question whose evaluator config is gone, rebuilt from what the run
// counted, so its answers still read.
function fallbackItem(configId, question, counts = {}) {
  const labels = Object.keys(counts).filter((label) => !label.startsWith("__"));
  const yesNo = labels.every((label) => YES_NO_LABELS.includes(label));
  return {
    configId,
    question,
    answerType: yesNo ? ANSWER_TYPES.YES_NO : ANSWER_TYPES.CATEGORY,
    labels: yesNo ? YES_NO_LABELS : labels,
    definitions: {},
  };
}

/**
 * A past Instant eval run as the runs endpoint returns it. Its questions are
 * matched back to their saved evaluators by config id (labels, definitions,
 * template), which is all its results need to be rebuilt. `rows` is how many
 * rows the task named: users, for a user question, with `sessions` the
 * sessions it read of them.
 */
export function historyRun(run, configsById = {}) {
  const items = (run.config_ids || []).map(
    (configId, index) =>
      configsById[configId] ||
      fallbackItem(
        configId,
        run.questions?.[index] || "",
        run.counts?.[configId],
      ),
  );
  return {
    taskId: run.task_id,
    name: run.name || "",
    status: run.status,
    createdAt: run.created_at,
    unit: run.unit,
    reads: run.reads,
    rows: typeof run.rows === "number" && run.rows > 0 ? run.rows : null,
    sessions: typeof run.sessions === "number" ? run.sessions : null,
    items,
    counts: run.counts || {},
    answered: run.answered || 0,
    pending: run.pending || 0,
    // When it last moved: its last answer, or its task's last change.
    lastActivityAt: run.last_activity_at || null,
  };
}

/**
 * Past Instant eval runs of this project and view, newest first, page by page,
 * each with its answer split. `search` matches the run's question.
 */
export function useInstantEvalHistory(
  projectId,
  configsById,
  { enabled = true, unit, search = "" } = {},
) {
  const query = useInfiniteQuery({
    queryKey: [HISTORY_KEY, projectId, unit || "", search],
    enabled: Boolean(projectId) && enabled,
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }) => {
      const response = await axios.get(endpoints.project.instantEvalRuns(), {
        params: {
          project_id: projectId,
          ...(unit ? { unit } : {}),
          ...(search ? { search } : {}),
          page_number: pageParam,
          page_size: HISTORY_PAGE_SIZE,
        },
        signal,
      });
      const data = result(response) || {};
      return {
        page: pageParam,
        runs: data.runs || [],
        hasMore: Boolean(data.has_more),
      };
    },
    getNextPageParam: (last) => (last.hasMore ? last.page + 1 : undefined),
    staleTime: 10_000,
    refetchInterval: (current) => historyRefetchMs(current.state.data?.pages),
    // The rail and the page say what went wrong; a toast would say it again.
    meta: { errorHandled: true },
  });
  const runs = useMemo(
    () =>
      (query.data?.pages || [])
        .flatMap((page) => page.runs)
        .filter(isHistorical)
        .map((run) => historyRun(run, configsById)),
    [query.data, configsById],
  );
  return {
    runs,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: Boolean(query.hasNextPage),
    isFetchingMore: query.isFetchingNextPage,
    loadMore: query.fetchNextPage,
    refetch: query.refetch,
  };
}
