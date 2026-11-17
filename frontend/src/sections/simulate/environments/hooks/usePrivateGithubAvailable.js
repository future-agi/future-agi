import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

/**
 * Whether this deployment offers private GitHub sources, as the backend's own
 * containment check answers it (`private_github_available` on the harness
 * health endpoint). `false` means job creation would refuse a private source.
 * `undefined` while loading, on an error, or from a server that does not send
 * the field: callers then keep today's form, so a self-hosted install is never
 * blocked by a missing answer.
 */
export default function usePrivateGithubAvailable() {
  const { data } = useQuery({
    queryKey: ["harness-jobs", "health"],
    queryFn: () => axios.get(apiPath("/simulate/api/harness-jobs/health/")),
    select: (res) => res.data?.private_github_available,
    staleTime: 5 * 60 * 1000,
    retry: false,
    meta: { errorHandled: true },
  });
  return typeof data === "boolean" ? data : undefined;
}
