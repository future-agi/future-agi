import { useOutletContext, useParams } from "react-router-dom";
import RunDetail from "./detail/RunDetail";

// The run/execution detail. On the execution route the workspace early-returns
// its Outlet, so this renders as its own full page (no workspace tab rail). The
// environment (and its client-side state) are resolved once by
// EnvironmentWorkspace and handed down through the Outlet context, so this
// route element only reads the run identity from the URL and renders the
// designer-style RunDetail — no second environment resolution.
//
// The nested `call-details` / `performance` / `analytics` child routes are now
// unused: RunDetail owns its own internal tabs. They are left registered for
// backwards-compatible deep links (they render nothing without an Outlet here)
// and can be removed in a follow-up.
export default function WorkspaceExecutionDetail() {
  const { env, envState } = useOutletContext() || {};
  const { testId, executionId } = useParams();

  if (!env) return null;

  return (
    <RunDetail
      env={env}
      envState={envState}
      testId={testId}
      executionId={executionId}
    />
  );
}
