import { useTheme } from "@emotion/react";
import { Tab, Tabs } from "@mui/material";
import React, { useMemo, useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AGENT_PLAYGROUND_TABS } from "../utils/constants";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import {
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
} from "../store";

export default function AgentPlayGroundTabs() {
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const { versionId, isDraftCreating } = useAgentPlaygroundStoreShallow(
    (s) => ({
      versionId: s.currentAgent?.version_id,
      isDraftCreating: s._isDraftCreating,
    }),
  );
  const { isRunning, openExecutionStopDialog } = useWorkflowRunStoreShallow(
    (s) => ({
      isRunning: s.isRunning,
      openExecutionStopDialog: s.openExecutionStopDialog,
    }),
  );

  // Get current tab from URL
  const currentTab = useMemo(() => {
    const pathSegments = location.pathname.split("/").filter(Boolean);
    const lastSegment = pathSegments[pathSegments.length - 1];
    return (
      AGENT_PLAYGROUND_TABS.find((tab) => tab.id === lastSegment) ||
      AGENT_PLAYGROUND_TABS[0]
    );
  }, [location.pathname]);

  const handleTabChange = useCallback(
    (event, newTabId) => {
      if (isDraftCreating) return;

      const selectedTab = AGENT_PLAYGROUND_TABS.find(
        (tab) => tab.id === newTabId,
      );
      if (!selectedTab) return;

      const params = versionId ? `?version=${versionId}` : "";
      const path = `${selectedTab.id}${params}`;

      if (isRunning) {
        openExecutionStopDialog(() => navigate(path, { replace: true }));
        return;
      }

      navigate(path, { replace: true });
    },
    [navigate, versionId, isRunning, isDraftCreating, openExecutionStopDialog],
  );

  return (
    <Tabs
      value={currentTab?.id || AGENT_PLAYGROUND_TABS[0]?.id}
      onChange={handleTabChange}
      textColor="primary"
      scrollButtons={false}
      sx={{
        "& .MuiTabs-flexContainer": {
          gap: 0,
        },
        "& .MuiTab-root": {
          paddingX: theme.spacing(1.5),
          margin: theme.spacing(0),
          marginRight: theme.spacing(0) + "!important",
          minWidth: "auto",
          transition: theme.transitions.create(["color", "background-color"], {
            duration: theme.transitions.duration.short,
          }),
          "&:first-of-type": {
            marginLeft: 0,
          },
        },
      }}
    >
      {AGENT_PLAYGROUND_TABS.map((tab) => {
        return (
          <Tab
            key={tab.id}
            label={tab.title || tab.label}
            value={tab.id}
            disabled={isDraftCreating && tab.id !== currentTab?.id}
            icon={
              tab.iconSrc ? (
                <SvgColor src={tab.iconSrc} />
              ) : (
                <Iconify icon={tab.icon} width={20} />
              )
            }
          />
        );
      })}
    </Tabs>
  );
}
