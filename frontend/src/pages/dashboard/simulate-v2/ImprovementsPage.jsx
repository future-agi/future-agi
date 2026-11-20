import { Helmet } from "react-helmet-async";
import { SimStoreProvider } from "src/sections/simulate-v2/store";
import ImprovementSourcesList from "src/sections/simulate-v2/improvements/ImprovementSourcesList";

/**
 * Improvements — level 1 · every environment (or dataset) with improvement runs.
 * Wraps the list in a SimStoreProvider so the page hydrates the same
 * env state the workspace routes read from. Rows drill into level 2.
 */
export default function ImprovementsPage() {
  return (
    <SimStoreProvider>
      <Helmet><title>Epochs · Future AGI</title></Helmet>
      <ImprovementSourcesList />
    </SimStoreProvider>
  );
}
