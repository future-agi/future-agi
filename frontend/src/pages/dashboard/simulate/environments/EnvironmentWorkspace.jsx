import { Helmet } from "react-helmet-async";
import EnvironmentWorkspace from "src/sections/simulate/environments/workspace/EnvironmentWorkspace";

export default function EnvironmentWorkspacePage() {
  return (
    <>
      <Helmet>
        <title>Environment | Future AGI</title>
      </Helmet>
      <EnvironmentWorkspace />
    </>
  );
}
