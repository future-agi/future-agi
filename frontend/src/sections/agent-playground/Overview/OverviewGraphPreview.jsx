import { ReactFlowProvider } from "@xyflow/react";
import React, { useCallback, useMemo } from "react";
import { Box, Button, CircularProgress, Typography } from "@mui/material";
import {
  PreviewContainer,
  PreviewGraphInner,
} from "src/sections/agent-playground/components/PreviewGraphInner";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { useAgentPlaygroundStoreShallow } from "../store";
import { enqueueSnackbar } from "notistack";
import { useNavigate, useParams } from "react-router";
import {
  useGetVersionDetail,
  useActivateVersion,
} from "src/api/agent-playground/agent-playground";
import { parseVersionResponse } from "../utils/versionPayloadUtils";
import { VERSION_STATUS } from "../utils/constants";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import useCanEditAgent from "../hooks/useCanEditAgent";

export default function OverviewGraphPreview({
  selectedVersion,
  currentVersion,
}) {
  const misMatch = selectedVersion?.version_id !== currentVersion;
  const navigate = useNavigate();
  const { agentId } = useParams();

  const { updateVersion, currentAgent } = useAgentPlaygroundStoreShallow(
    (s) => ({
      updateVersion: s.updateVersion,
      currentAgent: s.currentAgent,
    }),
  );

  // Fetch version detail when a version is selected
  const { data, isLoading, isError } = useGetVersionDetail(
    currentAgent?.id,
    selectedVersion?.version_id,
  );

  // Parse API response into XYFlow nodes/edges
  const graphData = useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    return parseVersionResponse(data);
  }, [data]);

  const { mutate: activateVersion, isPending: isActivating } =
    useActivateVersion();
  const { canEditAgent } = useCanEditAgent();

  const navigateToBuild = useCallback(
    (versionStatus) => {
      const versionNumber =
        typeof selectedVersion.version_name === "string"
          ? selectedVersion.version_name.replace(/^Version\s+/i, "")
          : selectedVersion.version_name;
      updateVersion(selectedVersion.version_id, versionNumber, {
        version_status: versionStatus,
      });
      navigate(
        `/dashboard/agents/playground/${agentId}/build?version=${selectedVersion.version_id}`,
      );
    },
    [updateVersion, selectedVersion, navigate, agentId],
  );

  const handleRestoreVersion = () => {
    if (!canEditAgent) return;
    const status = data?.status;

    if (status === VERSION_STATUS.INACTIVE) {
      activateVersion(
        { graphId: currentAgent?.id, versionId: selectedVersion.version_id },
        {
          onSuccess: () => {
            navigateToBuild(VERSION_STATUS.ACTIVE);
            enqueueSnackbar(
              `${currentAgent?.name} agent ${selectedVersion?.version_name} restored`,
              { variant: "success" },
            );
          },
        },
      );
    } else {
      navigateToBuild(status);
      enqueueSnackbar(
        `${currentAgent?.name} agent ${selectedVersion?.version_name} restored`,
        { variant: "success" },
      );
    }
  };

  // Loading state
  if (isLoading && selectedVersion?.version_id) {
    return (
      <PreviewContainer
        centered
        sx={{ border: "none", height: "100%", width: "100%" }}
      >
        <CircularProgress size={24} />
      </PreviewContainer>
    );
  }

  // Error state
  if (isError) {
    return (
      <PreviewContainer
        isError
        centered
        sx={{ border: "none", height: "100%", width: "100%" }}
      >
        <Typography color="error" variant="body2">
          Failed to load graph preview
        </Typography>
      </PreviewContainer>
    );
  }

  // No version selected
  if (!selectedVersion?.version_id) {
    return (
      <PreviewContainer
        centered
        sx={{ border: "none", height: "100%", width: "100%" }}
      >
        <Typography variant="body2" color="text.secondary">
          Select a version to preview
        </Typography>
      </PreviewContainer>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        width: "100%",
        position: "relative",
      }}
    >
      <ShowComponent condition={misMatch}>
        <Box
          sx={{
            position: "absolute",
            top: 10,
            left: 10,
            zIndex: 10,
          }}
        >
          <CustomTooltip
            show={!canEditAgent}
            type=""
            size="small"
            title="You don't have permission to restore versions."
            arrow
          >
            <span>
              <Button
                sx={{
                  color: "primary.main",
                  px: 0.5,
                }}
                size="small"
                variant="outlined"
                onClick={handleRestoreVersion}
                disabled={!canEditAgent || isActivating}
              >
                {isActivating ? "Restoring..." : "Restore"}
              </Button>
            </span>
          </CustomTooltip>
        </Box>
      </ShowComponent>
      {graphData?.nodes?.length === 0 ? (
        <PreviewContainer
          centered
          sx={{
            border: "none",
            height: "100%",
          }}
        >
          <Typography variant="body2" color="text.secondary">
            No nodes in this version
          </Typography>
        </PreviewContainer>
      ) : (
        <PreviewContainer
          sx={{
            border: "none",
            height: "100%",
          }}
        >
          <ReactFlowProvider>
            <PreviewGraphInner
              nodes={graphData.nodes}
              edges={graphData.edges}
              backgroundVariant={null}
              canvasBackground={"var(--bg-paper)"}
              fitView
              fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
            />
          </ReactFlowProvider>
        </PreviewContainer>
      )}
    </Box>
  );
}

OverviewGraphPreview.propTypes = {
  selectedVersion: PropTypes.object.isRequired,
  currentVersion: PropTypes.string,
};
