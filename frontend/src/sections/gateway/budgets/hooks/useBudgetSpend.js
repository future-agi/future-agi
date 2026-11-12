import { useQueries } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

const SPEND_SUMMARY = apiPath("/agentcc/spend-summary/");

const EMPTY = { total_spend: 0, per_key: {}, per_user: {}, per_model: {} };

// The signed-in user's organization spend for each budget period, from the
// request logs (GET /agentcc/spend-summary/, which answers a console session
// with its own organization only): {period: {total_spend, per_key, ...}}.
export function useBudgetSpend(periods) {
  const results = useQueries({
    queries: (periods || []).map((period) => ({
      queryKey: ["agentcc-budget-spend", period],
      queryFn: async () => {
        const { data } = await axios.get(SPEND_SUMMARY, {
          params: { period },
        });
        const orgs = data?.result?.orgs || {};
        return Object.values(orgs)[0] || EMPTY;
      },
      staleTime: 30000,
    })),
  });

  const data = {};
  (periods || []).forEach((period, i) => {
    if (results[i]?.data) data[period] = results[i].data;
  });
  return {
    data,
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
  };
}
