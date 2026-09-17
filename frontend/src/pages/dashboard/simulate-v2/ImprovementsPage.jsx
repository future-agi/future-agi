import { Helmet } from "react-helmet-async";
import { SimStoreProvider } from "src/sections/simulate-v2/store";
import ImprovementsList from "src/sections/simulate-v2/improvements/ImprovementsList";

/**
 * Improvements — global list of every self-improvement run.
 *
 * Folds `envState.optimizations` across every env in the store plus seeded
 * Dataset-source records so the "Source" column has variety. Detail routes
 * (/improvements/:id) reopen the existing OptimizationRunView.
 *
 * Wraps its own SimStoreProvider so the page works from a cold nav — same
 * pattern SimulatedRunsPage uses.
 */
export default function ImprovementsPage() {
  return (
    <SimStoreProvider>
      <Helmet>
        <title>Improvements | Future AGI</title>
      </Helmet>
      <ImprovementsList />
    </SimStoreProvider>
  );
}
