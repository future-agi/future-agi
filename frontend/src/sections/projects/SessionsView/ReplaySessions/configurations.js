import { REPLAY_ITEMS } from "./constants";
import axios, { endpoints } from "src/utils/axios";

export const REPLAY_MODULES = {
  SESSIONS: "session",
  TRACES: "trace",
};
/**
 * Configuration presets for different replay contexts
 * Each preset defines:
 * - scenarioItems: Items to show in CreateScenarios modal
 * - modalTitle: Title for the modal
 * - apiEndpoints: API functions specific to this context
 * - textContent: All text content used in the component
 * - handlers: Custom handler functions
 */

/**
 * Default/Sessions configuration - used as fallback
 */
export const defaultReplayConfig = {
  module: REPLAY_MODULES.SESSIONS,
  scenarioItems: REPLAY_ITEMS,
  modalTitle: "Replay Sessions",
  apiEndpoints: {
    createScenarios: (data) => {
      const { replay_session_id: replaySessionId, ...rest } = data;
      return axios.post(
        endpoints.project.generateReplayScenarios(replaySessionId),
        rest,
      );
    },
    runSimulation: (data) => {
      return axios.post(endpoints.runTests.create, data);
    },
    // addToScenarioGroup: (data) => {
    //   return axios.post("https://jsonplaceholder.typicode.com/posts", data);
    // },
  },
  textContent: {
    buttonLabel: "Replay Sessions",
    createScenariosButton: "Create Scenarios",
    cancelButton: "Cancel",
    backButton: "Back",
    nextButton: "Next",
    addToScenarioGroupButton: "Add to scenario group",
    runSimulationButton: "Run Simulation",
    loadingTitle: "Please wait while we generate scenarios for replay",
    agentDefinitionDescription:
      "Agent definition has been created base on the sessions you chose",
    loadingDescription: "We are generating scenarios your scenarios for replay",
    loadingSubDescription: "This might take some time",
    agentDefLoadingTitle:
      "Please wait while we extract agent prompt to create agent definition",
    agentDefLoadingDescription: "We are creating agent definition for replay.",
    agentDefLoadingSubDescription: "This might take some time",
    scenariosCreatedSuccess: "Scenarios created successfully",
    simulationCreatedSuccess: "Simulation created successfully",
    scenariosAddedSuccess: "Scenarios added to scenario group successfully",
  },
  handlers: {
    onScenarioCreated: (_data) => {
      // Sessions-specific success handler
    },
    onSimulationCreated: () => {
      // Sessions-specific simulation handler
    },
    onScenariosAdded: () => {
      // Sessions-specific scenarios added handler
    },
  },
};

/**
 * Configuration for Sessions replay context (uses default)
 */
export const sessionsReplayConfig = {
  ...defaultReplayConfig,
};

/**
 * Configuration for Traces replay context
 */
export const tracesReplayConfig = {
  module: REPLAY_MODULES.TRACES,
  scenarioItems: REPLAY_ITEMS,
  modalTitle: "Replay Traces",
  apiEndpoints: {
    createScenarios: (data) => {
      const { replay_session_id: replaySessionId, ...rest } = data;
      return axios.post(
        endpoints.project.generateReplayScenarios(replaySessionId),
        rest,
      );
    },
    // runSimulation: (data) => {
    //   return axios.post("https://jsonplaceholder.typicode.com/posts", data);
    // },
    // addToScenarioGroup: (data) => {
    //   return axios.post("https://jsonplaceholder.typicode.com/posts", data);
    // },
  },
  textContent: {
    buttonLabel: "Replay Traces",
    createScenariosButton: "Create Scenarios",
    cancelButton: "Cancel",
    backButton: "Back",
    nextButton: "Next",
    agentDefinitionDescription:
      "Agent definition has been created base on the traces you chose",
    addToScenarioGroupButton: "Add to scenario group",
    runSimulationButton: "Run Simulation",
    loadingTitle: "Please wait while we replay your traces...",
    loadingDescription:
      "We are replaying your selected traces to run simulation",
    loadingSubDescription: "This might take some time",
    agentDefLoadingTitle:
      "Please wait while we create your trace agent definition...",
    agentDefLoadingDescription:
      "We are creating agent definition for trace replay.",
    agentDefLoadingSubDescription: "This might take some time",
    scenariosCreatedSuccess: "Scenarios created successfully",
    simulationCreatedSuccess: "Simulation created successfully",
    scenariosAddedSuccess: "Traces added to scenario group successfully",
  },
  handlers: {
    onScenarioCreated: (_data) => {
      // Traces-specific success handler
    },
    onSimulationCreated: () => {
      // Traces-specific simulation handler
    },
    onScenariosAdded: () => {
      // Traces-specific scenarios added handler
    },
  },
};

/**
 * Helper function to create custom configurations
 * @param {Object} overrides - Partial configuration to override defaults
 * @returns {Object} Merged configuration object
 */
export const createReplayConfig = (overrides = {}) => {
  return {
    ...defaultReplayConfig,
    ...overrides,
    scenarioItems: overrides.scenarioItems || defaultReplayConfig.scenarioItems,
    modalTitle: overrides.modalTitle || defaultReplayConfig.modalTitle,
    apiEndpoints: {
      ...defaultReplayConfig.apiEndpoints,
      ...overrides.apiEndpoints,
    },
    textContent: {
      ...defaultReplayConfig.textContent,
      ...overrides.textContent,
    },
    handlers: {
      ...defaultReplayConfig.handlers,
      ...overrides.handlers,
    },
  };
};
