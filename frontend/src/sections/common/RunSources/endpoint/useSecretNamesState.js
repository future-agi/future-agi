// The workspace's secret names for the one secret picker (W6 B1/B4): GET
// secrets, first page. Names only; the mock never stores values.
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/** The workspace's secret names; null while loading. */
export function useSecretNamesState() {
  const { data, isSuccess } = useQuery({
    queryKey: ["secrets", "r4-names"],
    queryFn: () => axios.get(endpoints.secrets.list, { params: { page: 1 } }),
    select: (res) => (res?.data?.results ?? []).map((s) => s.name),
    staleTime: 60_000,
  });
  return isSuccess ? data ?? [] : null;
}

export default useSecretNamesState;
