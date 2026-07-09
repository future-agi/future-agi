import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const PLANS_QUERY_KEY = ["v2-plans-and-addons"];

export function usePlansAndAddons(enabled = true) {
  return useQuery({
    queryKey: PLANS_QUERY_KEY,
    queryFn: () => axios.get(endpoints.settings.v2.plansAndAddons),
    select: (res) => res.data?.result,
    enabled,
  });
}
