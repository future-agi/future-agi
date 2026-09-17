import { Helmet } from "react-helmet-async";
import { SimStoreProvider } from "src/sections/simulate-v2/store";
import ImprovementDetail from "src/sections/simulate-v2/improvements/ImprovementDetail";

/**
 * One improvement run — winner, trials, per-scenario breakdown.
 *
 * Renders the existing OptimizationRunView (the same one FixMyAgentDrawer
 * opens) inside a page shell so the URL is shareable and the browser
 * back-stack works.
 */
export default function ImprovementDetailPage() {
  return (
    <SimStoreProvider>
      <Helmet>
        <title>Self improvement run | Future AGI</title>
      </Helmet>
      <ImprovementDetail />
    </SimStoreProvider>
  );
}
