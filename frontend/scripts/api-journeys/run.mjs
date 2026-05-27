import { annotationQueueJourneys } from "./journeys/annotation-queues.mjs";
import { agentPlaygroundJourneys } from "./journeys/agent-playground.mjs";
import { appCoreJourneys } from "./journeys/app-core.mjs";
import { datasetEvalJourneys } from "./journeys/datasets-evals.mjs";
import { observeFilterJourneys } from "./journeys/observe-filters.mjs";
import { simulationAgentccJourneys } from "./journeys/simulation-agentcc.mjs";
import { runJourneys } from "./lib/runner.mjs";

const journeys = [
  ...appCoreJourneys,
  ...agentPlaygroundJourneys,
  ...annotationQueueJourneys,
  ...observeFilterJourneys,
  ...datasetEvalJourneys,
  ...simulationAgentccJourneys,
];

runJourneys(journeys).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
