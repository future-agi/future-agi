// Reads behind an imported row's source (row_meta.source): the traces of the
// Observe session a row was imported from. Each read is the tracer's own,
// scoped to the caller's organization and workspace; a session the caller
// cannot read is an error, never another tenant's data.
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { acceptedFields, bodyDefinitionOf } from "src/api/contracts/strictBody";

export const rowSourceKeys = {
  sessionTraces: (sessionId, projectId) => [
    "develop",
    "row-source-session",
    sessionId,
    projectId ?? null,
  ],
};

/** Loaded turns, oldest first, with explicit next-page and retry controls. */
export function useSourceSessionTraces(
  sessionId,
  { enabled = true, projectId } = {},
) {
  const definition = bodyDefinitionOf(
    "post",
    "/tracer/trace-session/{id}/query/",
  );
  // Older session-detail contracts reject project_id. Carry it once the
  // server declares it; the cache remains project-specific on either version.
  const project =
    projectId && acceptedFields(definition).includes("project_id")
      ? { project_id: projectId }
      : {};
  const query = useInfiniteQuery({
    queryKey: rowSourceKeys.sessionTraces(sessionId, projectId),
    initialPageParam: 0,
    queryFn: async ({ pageParam = 0, signal }) => {
      const res = await axios.post(
        endpoints.project.traceSessionQuery(sessionId),
        { page_number: pageParam, page_size: 50, ...project },
        { signal },
      );
      return res.data?.result ?? { response: [], next: false };
    },
    getNextPageParam: (lastPage, pages) =>
      lastPage.next ? pages.length : undefined,
    enabled: Boolean(sessionId) && enabled,
    meta: { errorHandled: true },
  });
  // Preserve the array interface used by Trajectory; pagination metadata is
  // retained alongside it, not discarded with the response envelope.
  return {
    ...query,
    data: query.data?.pages.flatMap((page) => page.response ?? []),
  };
}

/** True when a source read was refused (4xx): the source is gone or foreign. */
export function isRefusedSourceRead(error) {
  const status =
    error?.statusCode ?? error?.response?.status ?? error?.status ?? null;
  return Number.isFinite(status) && status >= 400 && status < 500;
}
