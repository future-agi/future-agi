import { useQuery, keepPreviousData } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { timeZoneParam } from "../../utils/timeZone";

const GA_KEY = "agentcc-guardrail-analytics";

export function useGuardrailOverview(params = {}) {
  return useQuery({
    queryKey: [GA_KEY, "overview", params],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.guardrailAnalytics.overview,
        { params },
      );
      return data.result || {};
    },
    staleTime: 60000,
    placeholderData: keepPreviousData,
  });
}

export function useGuardrailRules(params = {}) {
  return useQuery({
    queryKey: [GA_KEY, "rules", params],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.guardrailAnalytics.rules,
        { params },
      );
      const result = data.result || {};
      return Array.isArray(result) ? result : result.rules || [];
    },
    staleTime: 60000,
    placeholderData: keepPreviousData,
  });
}

/**
 * Matches per bucket: `{ series, time_zone }`, day buckets in the viewer's
 * zone when the API took it (utils/timeZone.js bucketTime).
 */
export function useGuardrailTrends(params = {}) {
  const query = { ...params, ...timeZoneParam() };
  return useQuery({
    queryKey: [GA_KEY, "trends", query],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.guardrailAnalytics.trends,
        { params: query },
      );
      const result = data.result || {};
      if (Array.isArray(result)) return { series: result };
      return { series: result.series || [], time_zone: result.time_zone };
    },
    staleTime: 60000,
    placeholderData: keepPreviousData,
  });
}
