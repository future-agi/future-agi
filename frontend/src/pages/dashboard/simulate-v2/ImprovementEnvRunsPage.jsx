import { Helmet } from "react-helmet-async";
import { SimStoreProvider } from "src/sections/simulate-v2/store";
import ImprovementEnvRunsView from "src/sections/simulate-v2/improvements/ImprovementEnvRunsView";

/**
 * Improvements — level 2 · env-scoped runs lens. Shares the same
 * SimStore the env workspace uses, so anything added/edited here
 * propagates to the env and vice versa.
 */
export default function ImprovementEnvRunsPage() {
  return (
    <SimStoreProvider>
      <Helmet><title>Improvement · Future AGI</title></Helmet>
      <ImprovementEnvRunsView />
    </SimStoreProvider>
  );
}
