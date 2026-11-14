import { Helmet } from "react-helmet-async";
import EnvironmentWorkspace from "src/sections/simulate/environments/workspace/EnvironmentWorkspace";
import WorkspaceErrorBoundary from "src/sections/simulate/environments/workspace/WorkspaceErrorBoundary";

export default function EnvironmentWorkspacePage() {
  return (
    <>
      <Helmet>
        <title>Environment | Future AGI</title>
      </Helmet>
      <WorkspaceErrorBoundary>
        <EnvironmentWorkspace />
      </WorkspaceErrorBoundary>
    </>
  );
}
