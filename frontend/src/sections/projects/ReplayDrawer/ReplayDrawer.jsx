import React from "react";
import ReplayConfigurationProvider from "../SessionsView/ReplaySessions/ReplayConfigurationProvider";
import CreateScenarios from "../SessionsView/CreateScenarios";
import ReplaySessions from "../SessionsView/ReplaySessions";
import PropTypes from "prop-types";
import {
  defaultReplayConfig,
  REPLAY_MODULES,
  tracesReplayConfig,
} from "../SessionsView/ReplaySessions/configurations";
import { useReplaySessionsStoreShallow } from "../SessionsView/ReplaySessions/store";
import { OBSERVE_TABS } from "../common";
import { useBeforeUnload } from "src/hooks/useBeforeUnload";

export default function ReplayDrawer({ activeRoute, projectDetail, gridApi }) {
  const {
    openCreateScenarios,
    setOpenCreateScenarios,
    setReplayType,
    isReplayDrawerCollapsed,
    setIsReplayDrawerCollapsed,
    openReplaySessionDrawer,
    setOpenReplaySessionDrawer,
  } = useReplaySessionsStoreShallow((s) => ({
    openCreateScenarios: s.openCreateScenarios,
    setOpenCreateScenarios: s.setOpenCreateScenarios,
    setReplayType: s.setReplayType,
    isReplayDrawerCollapsed: s.isReplayDrawerCollapsed,
    setIsReplayDrawerCollapsed: s.setIsReplayDrawerCollapsed,
    openReplaySessionDrawer: s.openReplaySessionDrawer,
    setOpenReplaySessionDrawer: s.setOpenReplaySessionDrawer,
  }));

  const handleCreateScenario = (scenarioId) => {
    setOpenCreateScenarios(false);
    const module =
      activeRoute === OBSERVE_TABS.LLM_TRACING
        ? REPLAY_MODULES?.TRACES
        : REPLAY_MODULES?.SESSIONS;
    setReplayType(scenarioId);
    setOpenReplaySessionDrawer(module, true);
  };

  const isTraceOpen =
    openReplaySessionDrawer?.[REPLAY_MODULES?.TRACES] || false;

  // const currentState = isTraceOpen ? traceState : sessionsState;
  const configuration = isTraceOpen
    ? {
        ...tracesReplayConfig,
        projectDetail: {
          name: projectDetail?.name,
        },
      }
    : {
        ...defaultReplayConfig,
        projectDetail: {
          name: projectDetail?.name,
        },
      };

  useBeforeUnload(Object.values(openReplaySessionDrawer).some((val) => val));

  const isDrawerOpen = Object.values(openReplaySessionDrawer).some(
    (val) => val,
  );

  return (
    <ReplayConfigurationProvider config={configuration}>
      <CreateScenarios
        open={openCreateScenarios}
        onClose={() => setOpenCreateScenarios(false)}
        onScenarioItemClick={handleCreateScenario}
      />
      {isDrawerOpen && (
        <ReplaySessions
          isCollapsed={isReplayDrawerCollapsed?.[configuration?.module]}
          setIsCollapsed={(module, collapsed) =>
            setIsReplayDrawerCollapsed(module, collapsed)
          }
          gridApi={gridApi}
        />
      )}
    </ReplayConfigurationProvider>
  );
}

ReplayDrawer.propTypes = {
  activeRoute: PropTypes.string,
  projectDetail: PropTypes.object.isRequired,
  gridApi: PropTypes.object.isRequired,
};
