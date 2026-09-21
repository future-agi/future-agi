import { Helmet } from "react-helmet-async";
import { SimStoreProvider } from "src/sections/simulate-v2/store";
import ImprovementsList from "src/sections/simulate-v2/improvements/ImprovementsList";

/**
 * Improvements — level 1 · workspace-wide list of run sources.
 * Wraps the list in a SimStoreProvider so the page hydrates the same
 * env state the workspace routes read from. Rows drill into level 2
 * (env-scoped runs lens) or, for datasets, a placeholder for now.
 */
export default function ImprovementsPage() {
  return (
    <SimStoreProvider>
      <Helmet><title>Improvements · Future AGI</title></Helmet>
      <ImprovementsList />
    </SimStoreProvider>
  );
}
