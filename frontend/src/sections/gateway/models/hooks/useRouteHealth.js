import { useState } from "react";
import { useQueries } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

import {
  healthSince,
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

// Each route's last 24 hours, from the org's own request logs. Returns
// { items: [{ route, health }], isLoading, error }; the window starts when
// the page opens, so the figures hold still while the user reads them.
export function useRouteHealth(routes, modelId) {
  const [since] = useState(() => healthSince());
  const results = useQueries({
    queries: routes.map((route) => {
      const filter = routeLogFilter(route, { modelId, routes });
      return {
        queryKey: [ROUTE_HEALTH_KEY, filter, since],
        queryFn: () => readRouteHealth(getLogs, filter, since),
        staleTime: 60000,
        refetchOnWindowFocus: false,
        meta: { errorHandled: true },
      };
    }),
  });
  return {
    items: routes.map((route, i) => ({ route, health: results[i]?.data })),
    isLoading: results.some((r) => r.isLoading),
    error: results.find((r) => r.error)?.error || null,
  };
}
