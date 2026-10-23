import { createContext, useContext } from "react";

/*
  What a connect flow's final button says. "Connect agent" on its own; when
  the agent is being connected for a new environment, the environment starts
  building as soon as it's connected, so the button says that.
*/
export const CONNECT_LABEL = "Connect agent";
export const ConnectSubmitContext = createContext(CONNECT_LABEL);
export const useConnectSubmitLabel = () => useContext(ConnectSubmitContext) || CONNECT_LABEL;

/*
  Set while the agent is being connected for a new environment: the build
  starts from the connect form, so the form also asks how many scenarios to
  generate and must pass preflight first. Null on a plain connect, where
  preflight is optional and the count is asked when an environment is built.
    { scenarioCount, setScenarioCount }
*/
export const ConnectBuildContext = createContext(null);
export const useConnectBuild = () => useContext(ConnectBuildContext);
export const DEFAULT_BUILD_SCENARIOS = 10;
export const MAX_BUILD_SCENARIOS = 1000;
