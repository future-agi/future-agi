import React from "react";
import PropTypes from "prop-types";
import ReplayConfigurationContext from "./useReplayConfiguration";
import { defaultReplayConfig } from "./configurations";

/**
 * Provider component that supplies configuration to Replay components
 * Merges provided config with default fallbacks
 * @param {Object} props
 * @param {React.ReactNode} props.children - Child components
 * @param {Object} props.config - Configuration object containing:
 *   - scenarioItems: Array of scenario items for CreateScenarios
 *   - modalTitle: Title for CreateScenarios modal
 *   - apiEndpoints: Object with API endpoint functions
 *   - textContent: Object with text content overrides
 *   - handlers: Object with custom handler functions
 */
const ReplayConfigurationProvider = ({ children, config = {} }) => {
  // Merge provided config with defaults, preferring provided values
  const mergedConfig = {
    ...defaultReplayConfig,
    ...config,
    scenarioItems: config?.scenarioItems || defaultReplayConfig.scenarioItems,
    modalTitle: config?.modalTitle || defaultReplayConfig.modalTitle,
    apiEndpoints: {
      ...defaultReplayConfig.apiEndpoints,
      ...(config?.apiEndpoints || {}),
    },
    textContent: {
      ...defaultReplayConfig.textContent,
      ...(config?.textContent || {}),
    },
    handlers: {
      ...defaultReplayConfig.handlers,
      ...(config?.handlers || {}),
    },
  };

  return (
    <ReplayConfigurationContext.Provider value={mergedConfig}>
      {children}
    </ReplayConfigurationContext.Provider>
  );
};

ReplayConfigurationProvider.propTypes = {
  children: PropTypes.node.isRequired,
  config: PropTypes.shape({
    module: PropTypes.string,
    scenarioItems: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string.isRequired,
        title: PropTypes.string.isRequired,
        description: PropTypes.string.isRequired,
        iconSrc: PropTypes.string.isRequired,
      }),
    ),
    modalTitle: PropTypes.string,
    apiEndpoints: PropTypes.shape({
      createScenarios: PropTypes.func,
      addToScenarioGroup: PropTypes.func,
    }),
    textContent: PropTypes.shape({
      buttonLabel: PropTypes.string,
      createScenariosButton: PropTypes.string,
      cancelButton: PropTypes.string,
      backButton: PropTypes.string,
      nextButton: PropTypes.string,
      addToScenarioGroupButton: PropTypes.string,
      runSimulationButton: PropTypes.string,
      loadingTitle: PropTypes.string,
      loadingDescription: PropTypes.string,
      loadingSubDescription: PropTypes.string,
      agentDefLoadingTitle: PropTypes.string,
      agentDefLoadingDescription: PropTypes.string,
      agentDefLoadingSubDescription: PropTypes.string,
      scenariosCreatedSuccess: PropTypes.string,
      simulationCreatedSuccess: PropTypes.string,
      scenariosAddedSuccess: PropTypes.string,
      agentDefinitionDescription: PropTypes.string,
    }),
    handlers: PropTypes.shape({
      onScenarioCreated: PropTypes.func,
      onSimulationCreated: PropTypes.func,
      onScenariosAdded: PropTypes.func,
    }),
  }),
};

ReplayConfigurationProvider.defaultProps = {
  config: {},
};

export default ReplayConfigurationProvider;
