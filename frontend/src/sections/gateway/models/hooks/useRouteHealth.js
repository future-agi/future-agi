import { useState } from "react";
import { useQueries } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

import {
  HEALTH_WINDOW_HOURS,
  readRouteHealth,
  routeLogFilter,
} from "../detail/routeHealth";

const ROUTE_HEALTH_KEY = "agentcc-model-route-health";

const getLogs = async (params) => {
  const { data } = await axios.get(endpoints.gateway.requestLogs, {
    params,
    timeout: 30000,
  });
  return data;
};

function newWindow(selection, revision = 0, previous = {}) {
  const now = Date.now();
  return {
    selection,
    revision,
    since: new Date(now - HEALTH_WINDOW_HOURS * 3600 * 1000).toISOString(),
    until: new Date(now).toISOString(),
    previous,
  };
}

// One bounded 24-hour window for the selection. Refresh all starts a new
// cohort; Retry refetches only its route in that cohort. Previous successful
// metrics survive a failed refresh, never a change of model/routes.
export function useRouteHealth(routes, modelId) {
  const filters = routes.map((route) => routeLogFilter(route, { modelId, routes }));
  const selection = JSON.stringify([modelId, routes.map((route, i) => [route.key, filters[i]])]);
  const [stored, setWindow] = useState(() => newWindow(selection));
  let window = stored;
  if (stored.selection !== selection) {
    // Reset during render, before children can see the previous selection's
    // figures. Equivalent newly allocated route objects keep their window.
    window = newWindow(selection, stored.revision + 1);
    setWindow(window);
  }
  const results = useQueries({
    queries: filters.map((filter) => ({
      queryKey: [ROUTE_HEALTH_KEY, selection, filter, window.since, window.until, window.revision],
      queryFn: () => readRouteHealth(getLogs, filter, window.since, window.until),
      staleTime: 60000,
      retry: false,
      refetchOnWindowFocus: false,
      meta: { errorHandled: true },
    })),
  });
  const items = routes.map((route, i) => {
    const result = results[i];
    const previous = window.previous[route.key];
    const health = result.data ?? previous?.health;
    return {
      route,
      health,
      status: result.status,
      isLoading: result.isLoading,
      isFetching: result.isFetching,
      error: result.error || null,
      refetch: result.refetch,
      dataUpdatedAt: result.data !== undefined
        ? result.dataUpdatedAt
        : previous?.dataUpdatedAt || 0,
      isStale: Boolean(health && (result.error || result.data === undefined)),
    };
  });
  const refreshAll = () => {
    const previous = Object.fromEntries(items
      .filter((item) => item.health !== undefined)
      .map((item) => [item.route.key, {
        health: item.health,
        dataUpdatedAt: item.dataUpdatedAt,
      }]));
    setWindow((current) => current.selection === selection
      ? newWindow(selection, current.revision + 1, previous)
      : current);
  };
  return {
    items,
    isLoading: results.some((result) => result.isLoading),
    isFetching: results.some((result) => result.isFetching),
    error: results.find((result) => result.error)?.error || null,
    refreshAll,
  };
}
