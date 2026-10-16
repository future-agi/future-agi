import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints, readQuery } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import {
  MAX_POPULATION,
  collectPopulation,
} from "src/sections/projects/observePopulation";
import { isRunActive } from "./insightsLenses";
import { isFixedScope } from "./insightsScope";

// Reads and writes of the Insights endpoints. Every response is
// {status, result}; the hooks hand back `result`.

export const insightsKeys = {
  all: ["insights"],
  configs: (projectId) => ["insights", "configs", projectId],
  config: (configId) => ["insights", "config", configId],
  run: (runId) => ["insights", "run", runId],
  units: (runId, params) => ["insights", "units", runId, params],
  population: (unit, projectId, filters, limit) => [
    "insights",
    "population",
    unit,
    projectId,
    JSON.stringify(filters || []),
    limit,
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

export const patchConfig = async (configId, body) =>
  result(await axios.patch(endpoints.project.insightsConfig(configId), body));

export const deleteConfig = async (configId) =>
  result(await axios.delete(endpoints.project.insightsConfig(configId)));

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

const messageOf = (error) =>
  [error?.message, error?.detail, error?.result, error?.error].find(
    (value) => typeof value === "string" && value.trim(),
  ) || "";

/**
 * What to tell the user when an Insights call fails. Plan, rate-limit and
 * model-availability messages are written for users and shown as they are;
 * anything else goes through the app's safe-message filter.
 */
export function insightsErrorMessage(
  error,
  fallback = "Something went wrong.",
) {
  const status = Number(error?.statusCode || error?.response?.status);
  const code = error?.code;
  if (status === 402 || code === "CAPABILITY_DENIED")
    return "Insights isn't available on this plan.";
  const message = messageOf(error).trim();
  const userFacing =
    status === 429 ||
    status === 503 ||
    code === "rate_limited" ||
    code === "feature_unavailable";
  if (userFacing && message && message.length <= 240 && !/[\r\n]/.test(message))
    return message;
  return getSafeActionErrorMessage(error, fallback);
}

export function useInsightsConfigs(projectId) {
  return useQuery({
    queryKey: insightsKeys.configs(projectId),
    queryFn: ({ signal }) => fetchConfigs(projectId, signal),
    enabled: Boolean(projectId),
    refetchInterval: (query) =>
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
      isRunActive(query.state.data?.latest_run?.status) ? LIST_POLL_MS : false,
    meta: { errorHandled: true },
  });
}

/**
 * A run, polled every two seconds while it works. When it finishes, the
 * report's run list (pager, trend) and the reports home refresh.
 */
export function useInsightsRun(runId) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: insightsKeys.run(runId),
    queryFn: ({ signal }) => fetchRun(runId, signal),
    enabled: Boolean(runId),
    refetchInterval: (q) =>
      isRunActive(q.state.data?.status) ? RUN_POLL_MS : false,
    refetchOnWindowFocus: false,
    meta: { errorHandled: true },
  });
  const status = query.data?.status;
  const configId = query.data?.config_id;
  const previous = useRef(status);
  useEffect(() => {
    if (isRunActive(previous.current) && status && !isRunActive(status)) {
      if (configId)
        queryClient.invalidateQueries({
          queryKey: insightsKeys.config(configId),
        });
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
    }
    previous.current = status;
  }, [status, configId, queryClient]);
  return query;
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
 * Ask a question about a run: {question, history: [{role, content}]}.
 * Resolves to the response's result ({answer, follow_ups}).
 */
export async function askRun(runId, body, signal) {
  const endpoint = endpoints.project.insightsRunAsk;
  if (typeof endpoint !== "function") {
    const error = new Error("Asking about a report isn't available yet.");
    error.code = "feature_unavailable";
    error.statusCode = 503;
    throw error;
  }
  return result(await axios.post(endpoint(runId), body, { signal }));
}

/**
 * How many units a scope holds, plus the first page of them (ids for the
 * draft and preview). Counting reads one page; a run collects up to
 * MAX_POPULATION.
 */
export const COUNT_LIMIT = 500;

export function usePopulationCount({
  unit,
  projectId,
  filters,
  enabled = true,
}) {
  return useQuery({
    queryKey: insightsKeys.population(unit, projectId, filters, COUNT_LIMIT),
    queryFn: ({ signal }) =>
      collectPopulation({
        unit,
        projectId,
        filters,
        limit: COUNT_LIMIT,
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

export const collectForScope = ({ unit, projectId, scope, signal }) =>
  collectPopulation({
    unit,
    projectId,
    filters: scope?.filters || [],
    limit: MAX_POPULATION,
    signal,
  });

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

/**
 * Whether a saved scope can be collected again from its filters. A scope
 * without filters ("All") reads the whole project; only a handed-over set
 * cannot be rebuilt.
 */
export const canRecollect = (scope) => !isFixedScope(scope);
