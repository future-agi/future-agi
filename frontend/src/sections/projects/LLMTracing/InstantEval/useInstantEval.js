import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  ANSWER_TYPES,
  INSTANT_EVAL_MODEL,
  YES_NO_LABELS,
  answerLabels,
  buildEvalMapping,
  buildTemplatePayload,
  cleanDefinitions,
} from "./instantEvalPayloads";
import {
  endUserIds,
  idsTaskPayload,
  normalizeReads,
  sessionIdsFromUserSessions,
  splitScopeFilters,
  unitMeta,
  userSessionsRequest,
} from "./instantEvalUnits";
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
  const configs = await listProjectConfigs(projectId);
  const existing = configs.map(itemFromConfig).filter(Boolean).reverse();
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
 * The rows a run names, by id. Traces, spans and sessions are the rows
 * themselves; a user question asks about each user's latest sessions in the
 * view's window. `items` are the rows the page shows from the start: for a
 * user question, the users who had a session.
 */
async function askedRows({ projectId, unit, items, filters, handedOver }) {
  const noun = unitMeta(unit).plural;
  const ids = items.map((item) => item.id);
  if (unit !== "users") {
    if (!ids.length)
      return { empty: `There are no ${noun} in this view to ask about.` };
    return { ids, items };
  }
  const userIds = endUserIds(ids);
  if (!userIds.length)
    return { empty: "There are no users in this view to ask about." };
  const response = await axios.post(
    endpoints.project.instantEvalUserSessions(),
    userSessionsRequest({
      projectId,
      userIds,
      // A hand-off names its users; their latest sessions are read whenever
      // they happened.
      filters: handedOver ? [] : filters,
    }),
  );
  const sessions = result(response)?.sessions || [];
  const sessionIds = sessionIdsFromUserSessions(result(response));
  if (!sessionIds.length)
    return { empty: "None of these users had a session in this window." };
  const withSessions = new Set(sessions.map((session) => session.end_user_id));
  return {
    ids: sessionIds,
    items: items.filter((item) => withSessions.has(item.id)),
    sessions: sessionIds.length,
  };
}

/**
 * Ask Jev the questions about these rows: save each question as a Jev
 * evaluator (or reuse the saved one) while the rows are named, then start one
 * eval task over exactly those ids, which the backend answers on its fast
 * path. Returns the task, the rows the page shows, or `empty` when there is
 * nothing to ask about.
 */
export async function startAsk({
  projectId,
  unit,
  questions,
  existingItems,
  reads,
  items,
  filters = [],
  handedOver = false,
}) {
  const runReads = normalizeReads(unit, reads || existingItems?.[0]?.reads);
  const evaluators = existingItems
    ? Promise.resolve(existingItems)
    : resolveEvaluators({ projectId, questions, reads: runReads, unit });
  // Saving doesn't depend on the rows; a failure surfaces below.
  evaluators.catch(() => {});
  const target = await askedRows({
    projectId,
    unit,
    items,
    filters,
    handedOver,
  });
  if (target.empty) return { empty: target.empty };
  const saved = await evaluators;
  if (saved.some((item) => !item.configId))
    throw new Error("The question could not be saved as an evaluator.");
  const response = await axios.post(
    endpoints.project.createEvalTask(),
    idsTaskPayload({ projectId, items: saved, unit, ids: target.ids }),
  );
  const taskId = result(response)?.id;
  if (!taskId) throw new Error("The run could not be started.");
  return {
    taskId,
    questions: saved,
    items: target.items,
    sessions: target.sessions ?? null,
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

// One live task per set of questions: re-creating it would answer every new
// trace twice and bill twice.
// Live tasks are found by name, page by page, so a long run history can't hide
// an existing one and cause a duplicate.
export async function findContinuousTask({ projectId, items }) {
  const wanted = new Set(items.map((item) => item.name).filter(Boolean));
  const matches = (task) =>
    task.run_type === "continuous" &&
    !["deleted", "failed"].includes(task.status) &&
    (task.evals_applied || []).length === wanted.size &&
    (task.evals_applied || []).every((name) => wanted.has(name));
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
  return undefined;
}

// Jev runs on the organization's own TypeSafe key; key problems point to the
// AI Providers settings where it is added or replaced.
export const AI_PROVIDERS_SETTINGS_PATH = "/dashboard/settings/ai-providers";

/**
 * Questions saved by Instant eval in this project. `items` has one entry per
 * question (the newest evaluator for it), optionally only those asked about
 * one unit; `all` keeps every evaluator for matching runs back to questions.
 */
export function useInstantEvals(projectId, { enabled = true, unit } = {}) {
  const query = useQuery({
    queryKey: ["instant-evals", projectId],
    enabled: Boolean(projectId) && enabled,
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskConfig(), {
        params: { project_id: projectId },
      }),
    select: (response) => {
      const configs = result(response) || [];
      return configs.map(itemFromConfig).filter(Boolean).reverse();
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

export const isLiveRun = (runItem) =>
  runItem.pending > 0 || LIVE_RUN_STATUSES.has(runItem.status);

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
 * rows the task named: sessions, for a user question.
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
    items,
    counts: run.counts || {},
    answered: run.answered || 0,
    pending: run.pending || 0,
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
    refetchInterval: (current) =>
      current.state.data?.pages.some((page) => page.runs.some(isLiveRun))
        ? HISTORY_LIVE_MS
        : false,
  });
  const runs = useMemo(
    () =>
      (query.data?.pages || [])
        .flatMap((page) => page.runs)
        // Live tasks ("Keep answering") share the name; History lists runs.
        .filter((run) => (run.run_type || "historical") === "historical")
        .map((run) => historyRun(run, configsById)),
    [query.data, configsById],
  );
  return {
    runs,
    isLoading: query.isLoading,
    isError: query.isError,
    hasMore: Boolean(query.hasNextPage),
    isFetchingMore: query.isFetchingNextPage,
    loadMore: query.fetchNextPage,
    refetch: query.refetch,
  };
}
