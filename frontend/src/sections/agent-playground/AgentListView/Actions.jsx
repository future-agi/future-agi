import { Box, Button, Stack, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";

import { useNavigate } from "react-router";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import SvgColor from "src/components/svg-color";
import {
  useAgentListGridStoreShallow,
  useAgentPlaygroundStoreShallow,
} from "../store";
import DeleteAgentsDialog from "../components/DeleteAgentsDialog";
import {
  useCreateGraph,
  useDeleteGraphs,
} from "../../../api/agent-playground/agent-playground";

import { isPrototypeMode } from "src/utils/prototype";

// L7c merge (walk sv2-run-cd d02): Create Agent posts an unmocked
// /agent-playground/graphs/ and opens a builder with no version, so the
// prototype build offers no Create Agent (the seeded Support Agent stays).

function SelectionActions({
  selectedCount,
  onDelete,
  onCancel,
  isDeleting = false,
}) {
  return (
    <Box
      sx={{
        padding: "6px 16px",
        gap: "16px",
        borderRadius: "4px",
        border: "1px solid",
        borderColor: "whiteScale.500",
        display: "flex",
      }}
    >
      <Typography
        typography="s1"
        fontWeight={"fontWeightRegular"}
        sx={{
          paddingRight: "16px",
          borderRight: "1px solid",
          borderColor: "whiteScale.500",
          color: (theme) =>
            theme.palette.mode === "dark" ? "text.primary" : "purple.500",
        }}
      >
        {selectedCount} Selected
      </Typography>
      <Typography
        role="button"
        typography="s1"
        fontWeight={"fontWeightRegular"}
        color="text.primary"
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "5px",
          cursor: isDeleting ? "not-allowed" : "pointer",
          opacity: isDeleting ? 0.6 : 1,
        }}
        onClick={() => {
          if (!isDeleting) {
            onDelete();
          }
        }}
      >
        <SvgColor
          src="/assets/icons/ic_delete.svg"
          sx={{
            height: "20px",
            width: "20px",
            bgcolor: "red.500",
          }}
        />
        Delete
      </Typography>

      <Typography
        role="button"
        typography="s1"
        fontWeight={"fontWeightRegular"}
        color="text.primary"
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          cursor: isDeleting ? "not-allowed" : "pointer",
          opacity: isDeleting ? 0.6 : 1,
          paddingLeft: "16px",
          borderLeft: "1px solid",
          borderColor: "whiteScale.500",
        }}
        onClick={() => {
          if (!isDeleting) {
            onCancel();
          }
        }}
      >
        Cancel
      </Typography>
    </Box>
  );
}

SelectionActions.propTypes = {
  selectedCount: PropTypes.number.isRequired,
  onDelete: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  isDeleting: PropTypes.bool,
};

export default function Actions({ searchQuery, setSearchQuery, gridApi }) {
  const navigate = useNavigate();
  const { setCurrentAgent } = useAgentPlaygroundStoreShallow((s) => ({
    setCurrentAgent: s.setCurrentAgent,
  }));
  const {
    totalRowCount,
    selectAll,
    toggledNodes,
    setSelectAll,
    setToggledNodes,
  } = useAgentListGridStoreShallow((state) => ({
    totalRowCount: state.totalRowCount,
    selectAll: state.selectAll,
    toggledNodes: state.toggledNodes,
    setSelectAll: state.setSelectAll,
    setToggledNodes: state.setToggledNodes,
  }));

  const userMadeSelection = useMemo(() => {
    const toggledNodesLength = toggledNodes?.length || 0;
    if (selectAll) {
      // When selectAll is true, check if there are any items actually selected
      // If toggleNodes contains all items, then nothing is selected
      return totalRowCount > 0 && toggledNodesLength < totalRowCount;
    }
    // When selectAll is false, check if any items are selected
    return toggledNodesLength > 0;
  }, [selectAll, toggledNodes, totalRowCount]);

  const selectedCount = useMemo(() => {
    const toggleNodesLength = toggledNodes?.length || 0;
    return selectAll ? totalRowCount - toggleNodesLength : toggleNodesLength;
  }, [selectAll, toggledNodes, totalRowCount]);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const deleteMutation = useDeleteGraphs({
    onSuccess: () => {
      setDeleteDialogOpen(false);
      setSelectAll(false);
      setToggledNodes([]);
      gridApi?.deselectAll();
      gridApi?.refreshServerSide();
    },
  });

  const onDelete = () => {
    setDeleteDialogOpen(true);
  };

  const handleDeleteConfirm = () => {
    deleteMutation.mutate(
      {
        selectAll,
        ...(selectAll
          ? { excludeIds: toggledNodes || [] }
          : { ids: toggledNodes || [] }),
      },
      {
        onError: () => {
          handleDeleteDialogClose();
        },
      },
    );
  };

  const handleDeleteDialogClose = () => {
    if (!deleteMutation.isPending) {
      setDeleteDialogOpen(false);
    }
  };

  const onCancel = () => {
    setSelectAll(false);
    setToggledNodes([]);
    gridApi?.deselectAll();
  };

  const { mutate: createAgent, isPending: isCreatingAgent } = useCreateGraph({
    navigate,
    setCurrentAgent,
  });

  const handleCreateAgent = () => {
    createAgent();
  };

  return (
    <Stack
      direction={"row"}
      justifyContent={"space-between"}
      alignItems={"center"}
    >
      <FormSearchField
        searchQuery={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        size="small"
        placeholder="Search"
        sx={{ minWidth: "360px" }}
      />
      {userMadeSelection ? (
        <SelectionActions
          selectedCount={selectedCount}
          onDelete={onDelete}
          onCancel={onCancel}
          isDeleting={deleteMutation.isPending}
        />
      ) : (
        <Stack direction={"row"} gap={2} alignItems={"center"}>
          <Button
            variant="outlined"
            size="small"
            sx={{ px: "8px" }}
            startIcon={
              <SvgColor
                sx={{
                  height: "20px",
                  width: "20px",
                }}
                src="/assets/icons/ic_docs_single.svg"
              />
            }
            component="a"
            href="https://docs.futureagi.com/docs/agent-playground"
            target="_blank"
          >
            View Docs
          </Button>

          {isPrototypeMode() ? null : (
            <LoadingButton
              variant="contained"
              color="primary"
              size="small"
              loading={isCreatingAgent}
              onClick={handleCreateAgent}
              startIcon={
                <SvgColor
                  src="/assets/icons/ic_add.svg"
                  sx={{
                    height: "20px",
                    width: "20px",
                  }}
                />
              }
            >
              Create Agent
            </LoadingButton>
          )}
        </Stack>
      )}
      <DeleteAgentsDialog
        open={deleteDialogOpen}
        onClose={handleDeleteDialogClose}
        onConfirm={handleDeleteConfirm}
        agentCount={selectedCount}
        isLoading={deleteMutation.isPending}
      />
    </Stack>
  );
}

Actions.propTypes = {
  searchQuery: PropTypes.string.isRequired,
  setSearchQuery: PropTypes.func.isRequired,
  gridApi: PropTypes.object,
};
