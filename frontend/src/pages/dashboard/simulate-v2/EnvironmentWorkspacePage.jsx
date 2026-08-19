import { Helmet } from "react-helmet-async";
import EnvironmentWorkspace from "src/sections/simulate-v2/workspace/EnvironmentWorkspace";

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
