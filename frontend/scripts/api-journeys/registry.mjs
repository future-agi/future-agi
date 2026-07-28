import { annotationQueueJourneys } from "./journeys/annotation-queues.mjs";
import { agentPlaygroundJourneys } from "./journeys/agent-playground.mjs";
import { appCoreJourneys } from "./journeys/app-core.mjs";
import { datasetEvalJourneys } from "./journeys/datasets-evals.mjs";
import { observeFilterJourneys } from "./journeys/observe-filters.mjs";
import { publicApiJourneys } from "./journeys/public-api.mjs";
import { simulationAgentccJourneys } from "./journeys/simulation-agentcc.mjs";

export const journeys = [
  ...appCoreJourneys,
  ...agentPlaygroundJourneys,
  ...annotationQueueJourneys,
  ...observeFilterJourneys,
  ...datasetEvalJourneys,
  ...simulationAgentccJourneys,
  ...publicApiJourneys,
];
