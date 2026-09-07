import { Helmet } from "react-helmet-async";
import BuildFromAgent from "src/sections/simulate-v2/environments/BuildFromAgent";

/**
 * Landing page for the "Build environment" click from the Create
 * Environments picker. Reuses BuildFromAgent, which reads
 * location.state.presetSource on mount and jumps straight into the
 * chat + derivation view.
 */
export default function BuildEnvironmentPage() {
  return (
    <>
      <Helmet>
        <title>Build environment | Future AGI</title>
      </Helmet>
      <BuildFromAgent />
    </>
  );
}
