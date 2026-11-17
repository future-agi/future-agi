import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const PLANS_QUERY_KEY = ["v2-plans-and-addons"];

// errorHandled: the caller shows a failed read itself (Plans & pricing shows
// "Plans couldn't be loaded"), so the app's query error handler adds no toast.
// An open reader without an inline failure (the upgrade dialog) keeps the
// toast. Every reader of this query writes its options to the one shared
// query, the last one to render winning, so a disabled reader (the closed
// upgrade dialog, always mounted) reports nothing of its own and must not
// bring the toast back over the page's own report.
export function usePlansAndAddons(
  enabled = true,
  { errorHandled = false } = {},
) {
  return useQuery({
    queryKey: PLANS_QUERY_KEY,
    queryFn: () => axios.get(endpoints.settings.v2.plansAndAddons),
    select: (res) => res.data?.result,
    enabled,
    meta: { errorHandled: errorHandled || !enabled },
  });
}
