import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useSharedIssueTrace } from "src/api/shared-links";
import { useSharedIssue } from "./sharedIssueContext";

// One trace's detail for the issue page. On a shared issue it reads through
// the share token; otherwise it is the signed-in trace detail read.
export function useIssueTraceDetail(traceId, options) {
  const shared = useSharedIssue();
  const own = useGetTraceDetail(shared ? null : traceId, options);
  const viaShare = useSharedIssueTrace(shared?.token, shared ? traceId : null);
  return shared ? viaShare : own;
}
