import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import React from "react";
import BackButton from "../../develop-detail/Common/BackButton";
import { useNavigate, useLocation } from "react-router";
import AgentName from "./AgentName";
import {
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
} from "../store";
import { DraftBadge } from "src/sections/workbench/createPrompt/SharedStyledComponents";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { ShowComponent } from "../../../components/show/ShowComponent";
import useDraftConfirmation from "../hooks/useDraftConfirmation";
import useCanEditAgent from "../hooks/useCanEditAgent";
import { validateGraphForSave } from "../utils/workflowValidation";
import { enqueueSnackbar } from "notistack";

export default function Header() {
  const navigate = useNavigate();
  const location = useLocation();
  const { isDraft } = useDraftConfirmation();
  const { canEditAgent } = useCanEditAgent();
  const isBuilderTab = location.pathname.endsWith("/build");
  const { isRunning, openExecutionStopDialog } = useWorkflowRunStoreShallow(
    (s) => ({
      isRunning: s.isRunning,
      openExecutionStopDialog: s.openExecutionStopDialog,
    }),
  );
  const {
    currentAgent,
    setOpenSaveAgentDialog,
    nodes,
    edges,
    isGraphReady,
    setValidationErrorNodeIds,
    clearValidationErrors,
  } = useAgentPlaygroundStoreShallow((s) => ({
    currentAgent: s.currentAgent,
    setOpenSaveAgentDialog: s.setOpenSaveAgentDialog,
    nodes: s.nodes,
    edges: s.edges,
    isGraphReady: s.isGraphReady,
    setValidationErrorNodeIds: s.setValidationErrorNodeIds,
    clearValidationErrors: s.clearValidationErrors,
  }));

  const handleSaveClick = () => {
    clearValidationErrors();
    const result = validateGraphForSave(nodes, edges);
    if (!result.valid) {
      if (result.invalidNodeIds.length > 0) {
        setValidationErrorNodeIds(result.invalidNodeIds);
      }
      const message = result.hasCycle
        ? result.errors[0].message
        : result.invalidNodeIds.length === 1
          ? "Node not configured"
          : `${result.invalidNodeIds.length} nodes are not configured`;
      enqueueSnackbar(message, { variant: "error" });
      return;
    }
    setOpenSaveAgentDialog(true);
  };

  return (
    <Stack
      direction={"row"}
      justifyContent={"space-between"}
      sx={{
        padding: 1.75,
      }}
    >
      <Stack direction={"row"} gap={2} alignItems={"center"}>
        <BackButton
          onBack={() => {
            if (isRunning) {
              openExecutionStopDialog(() => navigate(-1));
              return;
            }
            navigate(-1);
          }}
        />
        {currentAgent?.name ? (
          <AgentName currentAgent={currentAgent} />
        ) : (
          <Skeleton animation="pulse" variant="text" width={100} height={42} />
        )}
        <Box
          sx={{
            height: "30px",
            width: "54px",
            borderRadius: "100px",
            bgcolor: "purple.o10",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: (theme) => theme.spacing(0.5, 1),
          }}
        >
          <Typography
            color={"purple.500"}
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
          >
            Agent
          </Typography>
        </Box>
        {currentAgent?.version_name ? (
          <Typography
            sx={{
              flexShrink: 0,
            }}
            typography={"s2"}
            fontWeight={"fontWeightRegular"}
            color={"text.disabled"}
          >
            {currentAgent.version_name}
          </Typography>
        ) : (
          <Skeleton variant="text" width={80} height={24} />
        )}
        <ShowComponent condition={currentAgent?.is_draft}>
          <DraftBadge>Draft</DraftBadge>
        </ShowComponent>
      </Stack>
      <Stack direction={"row"} gap={2} alignItems={"center"}>
        {/* TODO: Uncomment when agent playground docs URL is available
        <Button
          variant="text"
          size="medium"
          sx={{
            color: "text.primary",
            padding: 1.5,
            fontSize: "14px",
            height: "40.8px",
          }}
          startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
          component="a"
          href="https://docs.futureagi.com/docs/prompt"
          target="_blank"
        >
          Docs
        </Button>
        */}
        <CustomTooltip
          show={!canEditAgent}
          type=""
          size="small"
          title="You don't have permission to edit this agent."
          arrow
        >
          <span>
            <Button
              disabled={
                !canEditAgent ||
                !isDraft ||
                !isBuilderTab ||
                isRunning ||
                !isGraphReady
              }
              size="small"
              variant="contained"
              color="primary"
              onClick={handleSaveClick}
            >
              Save Agent
            </Button>
          </span>
        </CustomTooltip>
      </Stack>
    </Stack>
  );
}
