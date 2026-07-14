import { Box, Button, CircularProgress, Typography } from "@mui/material";
import React, { useMemo, useRef, useState } from "react";
import { dagreTransformAndLayout } from "src/components/GraphBuilder/common";
import GraphView from "src/components/GraphBuilder/GraphView";
import PropTypes from "prop-types";
import GraphBuilderDrawer from "src/components/GraphBuilder/GraphBuilderDrawer";
import SvgColor from "src/components/svg-color";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { ReactFlowProvider } from "@xyflow/react";
import { ValidateAndTransformGraphSchema } from "src/components/GraphBuilder/validation";
import { validateGraphConnectivity } from "src/components/GraphBuilder/connectivity";
import DisconnectedNodesToast from "src/components/GraphBuilder/DisconnectedNodesToast";
import { useGraphStore } from "src/components/GraphBuilder/store/graphStore";
import { hasGraphChanged } from "./common";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import logger from "src/utils/logger";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const GraphPreview = ({ scenario, agentType, viewOnly = false }) => {
  const { role } = useAuthContext();
  const isWriteDisabled =
    !RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const initialGraphValue = useRef(null);
  const [openConfirmClose, setOpenConfirmClose] = useState(false);
  const isLoading =
    scenario?.graph && Object.keys(scenario?.graph || {}).length > 0
      ? false
      : scenario?.status === "Processing";
  const { nodes, edges } = useMemo(() => {
    if (
      !scenario?.graph ||
      !scenario?.graph?.nodes ||
      !scenario?.graph?.edges
    ) {
      return { nodes: [], edges: [] };
    }
    const { nodes: newNodes, edges: newEdges } = dagreTransformAndLayout(
      scenario?.graph?.nodes,
      scenario?.graph?.edges,
    );
    initialGraphValue.current = {
      newNodes,
      newEdges,
    };
    return { nodes: newNodes, edges: newEdges };
  }, [scenario]);

  const [open, setOpen] = useState(false);

  const queryClient = useQueryClient();

  const { mutate: updateScenarioMutate, isPending: saveLoading } = useMutation({
    mutationFn: (data) =>
      axios.put(endpoints.scenarios.edit(scenario.id), data),
    onSuccess: () => {
      enqueueSnackbar("Scenario updated successfully", {
        variant: "success",
      });
      setOpen(false);
      setOpenConfirmClose(false);
      queryClient.invalidateQueries({
        queryKey: ["scenario-detail", scenario.id],
      });
    },
  });

  const handleClose = () => {
    if (
      hasGraphChanged(initialGraphValue?.current, {
        newNodes: useGraphStore.getState().nodes,
        newEdges: useGraphStore.getState().edges,
      })
    ) {
      setOpenConfirmClose(true);
    } else {
      setOpen(false);
    }
  };

  const handleSaveChanges = () => {
    const currentNodes = useGraphStore.getState().nodes;
    const currentEdges = useGraphStore.getState().edges;

    const validatedGraph = ValidateAndTransformGraphSchema().safeParse({
      nodes: currentNodes,
      edges: currentEdges,
    });

    if (!validatedGraph.success) {
      const messageString = validatedGraph.error?.errors
        ?.map((error) => error.message)
        .join(", ");
      enqueueSnackbar(messageString, { variant: "error" });
      setOpenConfirmClose(false);
      return;
    }

    const { orphanNames, orphanIds, noStartNode } = validateGraphConnectivity(
      currentNodes,
      currentEdges,
    );
    useGraphStore.getState().setOrphanHighlights(orphanIds);
    if (noStartNode) {
      enqueueSnackbar("Graph has no start node. Add one before saving.", {
        variant: "error",
      });
      setOpenConfirmClose(false);
      return;
    }
    if (orphanIds.length > 0) {
      enqueueSnackbar(<DisconnectedNodesToast names={orphanNames} />, {
        variant: "error",
      });
      setOpenConfirmClose(false);
      return;
    }
    updateScenarioMutate({
      graph: {
        ...scenario?.graph,
        nodes: validatedGraph.data.nodes,
        edges: validatedGraph.data.edges,
      },
      name: scenario.name,
    });
  };

  if (isLoading) {
    return (
      <Box
        sx={{
          flex: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 1,
          height: "100%",
        }}
      >
        <CircularProgress size={20} />
        <Typography typography="s1">We are processing the graph...</Typography>
      </Box>
    );
  }

  if (scenario?.status === "Failed") {
    return (
      <Box
        sx={{
          flex: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s1">
          There was an error generating the scenario graph
        </Typography>
      </Box>
    );
  }
  logger.debug("Rendering GraphPreview with scenario:", scenario);

  return (
    <React.Fragment>
      <Box
        sx={{
          flex: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          position: "relative",
        }}
      >
        {!viewOnly && !isWriteDisabled && (
          <Button
            size="small"
            variant="outlined"
            startIcon={<SvgColor src="/assets/icons/ic_edit_pencil.svg" />}
            sx={{
              position: "absolute",
              top: "10px",
              right: "10px",
              zIndex: 10,
              backgroundColor: "background.paper",
            }}
            onClick={() => setOpen(true)}
          >
            Edit
          </Button>
        )}
        <ReactFlowProvider>
          <GraphView nodes={nodes} edges={edges} />
        </ReactFlowProvider>
        <GraphBuilderDrawer
          open={open}
          agentType={agentType}
          onClose={handleClose}
          value={scenario?.graph}
          onChange={(data) => {
            updateScenarioMutate({ graph: data, name: scenario.name });
          }}
          saveLoading={saveLoading}
        />
      </Box>
      <ModalWrapper
        open={openConfirmClose}
        onClose={() => setOpenConfirmClose(false)}
        onCancelBtn={() => {
          setOpen(false);
          setOpenConfirmClose(false);
        }}
        onSubmit={handleSaveChanges}
        title={"Unsaved Changes"}
        subTitle={
          "You have unsaved edits. Do you want to save them before leaving?"
        }
        cancelBtnTitle={"Discard changes"}
        actionBtnTitle={"Save changes"}
        isValid={true}
        isLoading={saveLoading}
        actionBtnProps={{
          color: "secondary",
        }}
        cancelBtnProps={{
          color: "error",
          variant: "contained",
        }}
        cancelBtnSx={{
          color: "secondary",
        }}
      />
    </React.Fragment>
  );
};

GraphPreview.propTypes = {
  scenario: PropTypes.object,
  viewOnly: PropTypes.bool,
  agentType: PropTypes.string,
};

export default GraphPreview;
