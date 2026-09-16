// The real preflight read: maps a redacted Phase-1 source draft to a
// HarnessPreflight body (T5), fires the one POST the backend exposes, and
// derives the ReadAudit view model (T6). A draft that cannot be preflighted
// for real resolves synchronously to a mock-only audit — no request.
//
// react-query is v5: a disabled query reports `isPending: true` forever, so the
// pending surface keys off `isFetching` (true only while a request is in
// flight). During a refetch `audit` still holds the previous data until the new
// response hydrates, so `ReadAudit` keeps showing it (no spinner).
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { preflightHarnessJob } from "src/api/harness/harness";
import { SIMULATE_ENVIRONMENTS_KEY } from "./environments";
import { draftToPreflightPayload } from "./preflightPayload";
import { preflightToReadAudit } from "./preflightReadAudit";

export const preflightQueryKey = (payload) => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "preflight",
  payload,
];

export function usePreflight(draft, { retriedSections = [] } = {}) {
  const { payload, skipped } = useMemo(
    () => draftToPreflightPayload(draft),
    [draft]
  );

  const query = useQuery({
    queryKey: preflightQueryKey(payload ?? { skipped }),
    queryFn: () => preflightHarnessJob(payload),
    enabled: !!draft && !!payload,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const audit = useMemo(
    () =>
      draft && (skipped || query.data || query.error)
        ? preflightToReadAudit({
            response: query.data,
            error: query.error,
            skipped,
            draft,
            retriedSections,
          })
        : null,
    [skipped, query.data, query.error, draft, retriedSections]
  );

  // No payload = a skipped draft whose query is disabled. react-query v5's
  // refetch() ignores `enabled` and would POST an empty body (→ server error),
  // so only hand out refetch when there is a real request to re-run.
  return {
    audit,
    isFetching: query.isFetching,
    refetch: payload ? query.refetch : undefined,
  };
}
