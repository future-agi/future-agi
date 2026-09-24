import { useLocation, useParams } from "react-router";

// The current URL up to and including the execution id, so the execution
// detail's internal links stay inside whichever shell mounted it (the
// standalone `/simulate/test/**` route or the environment workspace).
export default function useExecutionLinkBase() {
  const { executionId } = useParams();
  const { pathname } = useLocation();
  return pathname.slice(
    0,
    pathname.indexOf(`/${executionId}`) + executionId.length + 1,
  );
}
