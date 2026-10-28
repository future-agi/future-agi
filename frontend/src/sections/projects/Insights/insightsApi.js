import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints, readQuery } from "src/utils/axios";
import {
  RETRY_LATER,
  actionErrorMessage,
  errorStatus,
} from "src/sections/projects/requestErrors";
import { collectPopulation } from "src/sections/projects/observePopulation";
import { isRunActive } from "./insightsLenses";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

// Reads and writes of the Insights endpoints. Every response is
// {status, result}; the hooks hand back `result`.

export const insightsKeys = {
  all: ["insights"],
  configs: (projectId) => ["insights", "configs", projectId],
  config: (configId) => ["insights", "config", configId],
  run: (runId) => ["insights", "run", runId],
  units: (runId, params) => ["insights", "units", runId, params],
  population: (unit, projectId, scope) => [
    "insights",
    "population",
    unit,
    projectId,
    JSON.stringify(scope || {}),
  ],
  examples: (unit, projectId, filters) => [
    "insights",
    "examples",
    unit,
    projectId,
    JSON.stringify(filters || []),
  ],
};

const RUN_POLL_MS = 2000;
const LIST_POLL_MS = 5000;
export const UNITS_PAGE = 50;
const UNITS_MAX_PAGE = 200;

const result = (response) => response?.data?.result;

export const fetchConfigs = async (projectId, signal) =>
  result(
    await readQuery(endpoints.project.insightsConfigs(), {
      params: { project_id: projectId },
      signal,
    }),
  )?.configs || [];

export const fetchConfig = async (configId, signal) =>
  result(
    await readQuery(endpoints.project.insightsConfig(configId), { signal }),
  );

export const fetchRun = async (runId, signal) =>
  result(await readQuery(endpoints.project.insightsRun(runId), { signal }));

export const fetchUnits = async (runId, params = {}, signal) =>
  result(
    await readQuery(endpoints.project.insightsRunUnits(runId), {
      params: Object.fromEntries(
        Object.entries(params).filter(
          ([, value]) => value !== undefined && value !== null && value !== "",
        ),
      ),
      signal,
    }),
  ) || { units: [], total: 0 };

export const startRun = async (body, signal) =>
  result(await axios.post(endpoints.project.insightsRuns(), body, { signal }));

export const stopRun = async (runId, signal) =>
  result(
    await axios.post(endpoints.project.insightsRunStop(runId), undefined, {
      signal,
    }),
  );

export const patchConfig = async (configId, body) =>
  result(await axios.patch(endpoints.project.insightsConfig(configId), body));

export const deleteConfig = async (configId) =>
  result(await axios.delete(endpoints.project.insightsConfig(configId)));

/**
 * After a delete: drop the report from every cached list before the refetch
 * lands, so the page's auto-open never reopens it as "could not be loaded"
 * (and, with none left, shows the empty state).
 */
export function forgetConfig(queryClient, configId) {
  queryClient.setQueriesData({ queryKey: ["insights", "configs"] }, (list) =>
    Array.isArray(list) ? list.filter((c) => c.id !== configId) : list,
  );
  queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
  queryClient.removeQueries({ queryKey: insightsKeys.config(configId) });
}

export const draftConfig = async (body, signal) =>
  result(await axios.post(endpoints.project.insightsDraft(), body, { signal }));

export const previewConfig = async (body, signal) =>
  result(
    await axios.post(endpoints.project.insightsPreview(), body, { signal }),
  );

/** Every unit of a category (or of a run), paged at the endpoint's maximum. */
export async function collectRunUnits(runId, params = {}, signal) {
  const units = [];
  for (let offset = 0; ; offset += UNITS_MAX_PAGE) {
    // eslint-disable-next-line no-await-in-loop
    const page = await fetchUnits(
      runId,
      { ...params, offset, limit: UNITS_MAX_PAGE },
      signal,
    );
    units.push(...(page.units || []));
    if (!page.units?.length || units.length >= (page.total || 0)) break;
  }
  return units;
}

export const isAbort = (error) =>
  error?.name === "AbortError" ||
  error?.name === "CanceledError" ||
  error?.transportCode === "ERR_CANCELED" ||
  error?.code === "ERR_CANCELED";

// Codes whose message the server writes for users, shown whatever their
// status: limits, and what isn't available right now.
const USER_FACING_CODES = new Set([
  "rate_limited",
  "feature_unavailable",
  "insight_unavailable",
  "model_unavailable",
  "users_still_counting",
]);

// Refusals the capability service words for people: the feature by name and
// what unlocks it here (a license self-hosted, a plan on Cloud), and the
// Turing denial (Insights is allowed, the Turing models it runs on aren't).
const DENIAL_CODES = new Set([
  "CAPABILITY_DENIED",
  "LICENSE_MISSING",
  "LICENSE_INVALID",
  "LICENSE_EXPIRED",
  "LICENSE_TRIAL_EXPIRED",
  "LICENSE_FEATURE_MISSING",
  "FEATURE_NOT_IN_GRACE",
  "PLAN_FEATURE_MISSING",
]);

const messageOf = (error) =>
  [error?.message, error?.detail, error?.result, error?.error].find(
    (value) => typeof value === "string" && value.trim(),
  ) || "";

/**
 * A request refused for the credits it needs (a report run, a question about
 * a report, a draft, a preview): {message, sampleSize, creditsLeft}, or
 * null. The answer to it is a smaller sample or billing.
 */
export function insightsCreditRefusal(error) {
  const data = error?.response?.data || error || {};
  const status = Number(error?.statusCode || error?.response?.status);
  const code = data.code || error?.code;
  if (code !== "insights_credits" || (status !== 402 && status !== 403))
    return null;
  const result =
    data.result && typeof data.result === "object" ? data.result : {};
  return {
    message:
      result.message ||
      messageOf(data) ||
      "Your plan has no credits left for this.",
    sampleSize: result.sample_size ?? null,
    creditsLeft: result.credits_left ?? null,
  };
}

/**
 * What to tell the user when an Insights call fails. Plan, credit, rate-limit
 * and availability messages are written for users and shown as they are; a
 * fault on the server's side (a 5xx, or no answer) reads `retry`, never a
 * reason to change what was asked; any other refusal goes through the app's
 * safe-message filter, else `fallback`.
 */
export function insightsErrorMessage(
  error,
  fallback = "Something went wrong.",
  retry = RETRY_LATER,
) {
  const credits = insightsCreditRefusal(error);
  if (credits) return credits.message;
  const status = errorStatus(error);
  const code = error?.code;
  const message = messageOf(error).trim();
  const shown = message && message.length <= 240 && !/[\r\n]/.test(message);
  // Any other refusal (an older entitlement check) names no deployment.
  if (status === 402 || code === "CAPABILITY_DENIED")
    return DENIAL_CODES.has(code) && shown
      ? message
      : "Insights isn't available to this organization.";
  if (shown && (status === 429 || USER_FACING_CODES.has(code))) return message;
  // Refused for this person's role (a Viewer starting a run): the server
  // says so.
  if (shown && status === 403 && code === "permission_denied") return message;
  return actionErrorMessage(error, { fallback, retry });
}

/** A read refused because the project is gone (deleted, or not this
 * organization's): no retry brings it back. */
export const projectMissing = (error) => errorStatus(error) === 404;

export const runFailureReason = (run) =>
  getSafeActionErrorMessage(
    { statusCode: 400, result: run.error },
    "The report could not be completed. Try again in a moment.",
  );

/**
 * What a failed run offers next, by its `error_code`: someone stopped it
 * ("stopped": run it again), only an administrator can fix it ("license":
 * the License settings, which only a self-hosted install has; on Cloud the
 * reason names the plan. "model_setup": nothing to click, its reason says
 * what to change), or another try may work ("": Retry).
 */
export function failedRunNext(run, { cloud = false } = {}) {
  const code = run?.error_code || "";
  const stopped = code === "stopped";
  const needsAdmin = code === "license" || code === "model_setup";
  return {
    stopped,
    license: code === "license" && !cloud,
    rerun: stopped ? "Run again" : needsAdmin ? null : "Retry",
  };
}

// How long a stopped run's page waits for the reads it had under way: past
// this (a worker lost meanwhile), it gives the counts as they stand.
const STOPPING_MAX_MS = 2 * 60_000;

/**
 * A run just stopped whose reads already under way are still being saved
 * (`progress.in_flight`): its counts aren't final yet, so it reads as
 * stopping, with none, until they are.
 */
export function runStopping(run, now = Date.now()) {
  if (run?.status !== "failed" || run.error_code !== "stopped") return false;
  if (!Object.keys(run.progress?.in_flight || {}).length) return false;
  const since = new Date(run.finished_at).getTime();
  return Number.isFinite(since) && now - since < STOPPING_MAX_MS;
}

const creditsFrom = (data) =>
  data?.credits_estimate != null
    ? {
        estimate: data.credits_estimate,
        limited: Boolean(data.credit_limited),
        unavailable: Boolean(data.credit_check_unavailable),
        left: data.credits_left ?? null,
        maxSample: data.credit_max_sample ?? null,
        message: data.credit_message || null,
      }
    : null;

export const actionCreditsBlocked = (query) =>
  !query.isSuccess ||
  query.isFetching ||
  Boolean(query.data?.limited || query.data?.unavailable);

export function useActionCredits(projectId, action) {
  return useQuery({
    queryKey: ["insights", "action-credits", projectId, action],
    queryFn: async ({ signal }) =>
      creditsFrom(
        result(
          await axios.post(
            endpoints.project.insightsEstimate(),
            { project_id: projectId, action },
            { signal },
          ),
        ),
      ),
    enabled: Boolean(projectId),
    staleTime: 0,
    retry: false,
    meta: { errorHandled: true },
  });
}

export function useInsightsConfigs(projectId) {
  return useQuery({
    queryKey: insightsKeys.configs(projectId),
    queryFn: ({ signal }) => fetchConfigs(projectId, signal),
    enabled: Boolean(projectId),
    // Polling reads the last good data, so a failing read stops it until
    // the person retries.
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      (query.state.data || []).some((c) => isRunActive(c.latest_run?.status))
        ? LIST_POLL_MS
        : false,
    meta: { errorHandled: true },
  });
}

export function useInsightsConfig(configId) {
  return useQuery({
    queryKey: insightsKeys.config(configId),
    queryFn: ({ signal }) => fetchConfig(configId, signal),
    enabled: Boolean(configId),
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      isRunActive(query.state.data?.latest_run?.status)
        ? LIST_POLL_MS
        : false,
    meta: { errorHandled: true },
  });
}

// Judged as of the run's last read, so a run past STOPPING_MAX_MS settles
// on the read that finds it so.
const runLive = (run, readAt) =>
  isRunActive(run?.status) || runStopping(run, readAt);

/**
 * A run, polled every two seconds while it works or is being stopped. When
 * it ends, the report's run list (pager, trend) and the reports home
 * refresh.
 */
export function useInsightsRun(runId) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: insightsKeys.run(runId),
    queryFn: ({ signal }) => fetchRun(runId, signal),
    enabled: Boolean(runId),
    refetchInterval: (q) =>
      q.state.status !== "error" && runLive(q.state.data, q.state.dataUpdatedAt)
        ? RUN_POLL_MS
        : false,
    refetchOnWindowFocus: false,
    meta: { errorHandled: true },
  });
  const status = query.data?.status;
  const live = runLive(query.data, query.dataUpdatedAt);
  const configId = query.data?.config_id;
  const previous = useRef(live);
  useEffect(() => {
    if (previous.current && status && !live) {
      if (configId)
        queryClient.invalidateQueries({
          queryKey: insightsKeys.config(configId),
        });
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
    }
    previous.current = live;
  }, [status, live, configId, queryClient]);
  return query;
}

/**
 * The run a report card shows, as the report page last read it, read again
 * while it is being stopped: the card says "Stopping" as the page does and
 * then gives the same counts. Reads nothing for a run the page hasn't read.
 */
export function useStoppingRun(runId) {
  const stopping = (q) => runStopping(q.state.data, q.state.dataUpdatedAt);
  const query = useQuery({
    queryKey: insightsKeys.run(runId),
    queryFn: ({ signal }) => fetchRun(runId, signal),
    enabled: (q) => Boolean(runId) && stopping(q),
    refetchInterval: (q) =>
      q.state.status !== "error" && stopping(q) ? RUN_POLL_MS : false,
    refetchOnWindowFocus: false,
    meta: { errorHandled: true },
  });
  return {
    run: query.data,
    stopping: runStopping(query.data, query.dataUpdatedAt),
  };
}

export function useRunUnits(runId, params, { enabled = true } = {}) {
  return useQuery({
    queryKey: insightsKeys.units(runId, params),
    queryFn: ({ signal }) => fetchUnits(runId, params, signal),
    enabled: Boolean(runId) && enabled,
    staleTime: 60_000,
    placeholderData: (previousData) => previousData,
    meta: { errorHandled: true },
  });
}

/**
 * One analysed unit by id: for a citation's hover card, or with its full
 * content (`content: true`) for the example panel. A server (or API
 * contract) that cannot send the content yet falls back to the unit alone.
 */
export function useRunUnit(
  runId,
  unitId,
  { content = false, enabled = true } = {},
) {
  const params = content
    ? { unit_ids: unitId, include_content: true }
    : { unit_ids: unitId };
  const first = (page) => page.units?.[0] || null;
  return useQuery({
    queryKey: insightsKeys.units(runId, params),
    queryFn: async ({ signal }) => {
      try {
        return first(await fetchUnits(runId, { ...params, limit: 1 }, signal));
      } catch (error) {
        if (!content || isAbort(error)) throw error;
        return first(
          await fetchUnits(runId, { unit_ids: unitId, limit: 1 }, signal),
        );
      }
    },
    enabled: Boolean(runId && unitId) && enabled,
    staleTime: 60_000,
    meta: { errorHandled: true },
  });
}

/**
 * An image a unit's evidence cites (its `media` alias), as an object URL for
 * an <img>: the endpoint needs the session's credentials, so it is fetched,
 * never linked. The URL is revoked once the image is no longer shown.
 */
export function useCitedImage(runId, unitId, media) {
  const query = useQuery({
    queryKey: ["insights", "media", runId, unitId, media],
    queryFn: async ({ signal }) =>
      (
        await readQuery(endpoints.project.insightsRunMedia(runId), {
          params: { unit_id: unitId, media },
          responseType: "blob",
          signal,
        })
      ).data,
    enabled: Boolean(runId && unitId && media),
    staleTime: Infinity,
    // A busy server (503) is worth one more try; a refusal is final.
    retry: (count, error) => errorStatus(error) === 503 && count < 1,
    meta: { errorHandled: true },
  });
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!(query.data instanceof Blob)) return undefined;
    const next = URL.createObjectURL(query.data);
    setUrl(next);
    return () => {
      URL.revokeObjectURL(next);
      setUrl(null);
    };
  }, [query.data]);
  return { url, isError: query.isError };
}

/**
 * Ask a question about a run: {question, history: [{role, content}]}.
 * Resolves to the response's result ({answer, follow_ups}).
 */
export async function askRun(runId, body, signal) {
  return result(
    await axios.post(endpoints.project.insightsRunAsk(runId), body, { signal }),
  );
}

/**
 * How many units a scope ({filters, search?}) holds, counted on the server
 * the way a run reads them; a few of them the server picked for the draft
 * and preview; and what analysing a sample of `sampleSize` costs:
 * {total, totalIsLowerBound, sampleIds, credits}. Rows handed over by id
 * (`populationSize` of them) have nothing to count: only their cost is read.
 */
export const fetchScopeCount = async (
  { projectId, unit, scope, populationSize, sampleSize },
  signal,
) => {
  const data = result(
    await axios.post(
      endpoints.project.insightsEstimate(),
      {
        project_id: projectId,
        unit,
        ...(populationSize ? { population_size: populationSize } : { scope }),
        ...(sampleSize ? { sample_size: sampleSize } : {}),
      },
      { signal },
    ),
  );
  return {
    total: data?.population ?? 0,
    totalIsLowerBound: Boolean(data?.population_is_lower_bound),
    sampleIds: Array.isArray(data?.sample_ids)
      ? data.sample_ids.map(String)
      : null,
    // What the run costs, and whether the credits left pay for it.
    credits: creditsFrom(data),
  };
};

// A Users count that checks each user is counted a few seconds at a time:
// until it is exact it is read again this often, and shows as "N+".
const COUNTING_POLL_MS = 2000;

export function usePopulationCount({
  unit,
  projectId,
  scope,
  populationSize,
  sampleSize,
  enabled = true,
}) {
  return useQuery({
    queryKey: [
      ...insightsKeys.population(unit, projectId, scope),
      sampleSize,
      populationSize || null,
    ],
    queryFn: ({ signal }) =>
      fetchScopeCount(
        { projectId, unit, scope, populationSize, sampleSize },
        signal,
      ),
    enabled: Boolean(unit && projectId) && enabled,
    staleTime: 60_000,
    placeholderData: (previousData) => previousData,
    refetchInterval: (query) =>
      unit === "users" && query.state.data?.totalIsLowerBound
        ? COUNTING_POLL_MS
        : false,
    retry: false,
    meta: { errorHandled: true },
  });
}

// Rows the draft and preview read when the server picks none: the newest
// page of the scope's list.
export const EXAMPLES_LIMIT = 100;

export function useExampleRows({
  unit,
  projectId,
  filters,
  search,
  enabled = true,
}) {
  return useQuery({
    queryKey: [
      ...insightsKeys.examples(unit, projectId, filters),
      search || "",
    ],
    queryFn: ({ signal }) =>
      collectPopulation({
        unit,
        projectId,
        filters,
        search,
        limit: EXAMPLES_LIMIT,
        signal,
      }),
    enabled: Boolean(unit && projectId) && enabled,
    staleTime: 60_000,
    placeholderData: (previousData) => previousData,
    retry: false,
    meta: { errorHandled: true },
  });
}

// Whether the project has any sessions, to pick the default unit.
export function useHasSessions(projectId, enabled) {
  return useQuery({
    queryKey: ["insights", "has-sessions", projectId],
    queryFn: async ({ signal }) =>
      (
        await collectPopulation({
          unit: "sessions",
          projectId,
          filters: [],
          limit: 1,
          signal,
        })
      ).total > 0,
    enabled: Boolean(projectId) && enabled,
    staleTime: 5 * 60_000,
    retry: false,
    meta: { errorHandled: true },
  });
}

// A handed-over population (fixed scope) cannot be collected again from
// filters, so its rows are kept for this browser session under the report's
// id: "Run again" and "Retry" can then reuse them.
const POPULATION_PREFIX = "insights-population:";

export function rememberPopulation(configId, population) {
  try {
    globalThis.sessionStorage?.setItem(
      POPULATION_PREFIX + configId,
      JSON.stringify(population),
    );
  } catch {
    // Storage full or unavailable: a rerun then asks for a time window.
  }
}

export function recallPopulation(configId) {
  try {
    const raw = globalThis.sessionStorage?.getItem(
      POPULATION_PREFIX + configId,
    );
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) && parsed.length ? parsed : null;
  } catch {
    return null;
  }
}
