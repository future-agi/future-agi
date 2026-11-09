import SelectionBar from "src/components/selection-bar";
import { Button, Stack } from "@mui/material";
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

function SelectionActions({
  selectedCount,
  onDelete,
  onCancel,
  isDeleting = false,
}) {
  return (
    <SelectionBar
      count={selectedCount}
      onCancel={onCancel}
      actions={[
        {
          label: "Delete",
          icon: "solar:trash-bin-trash-linear",
          onClick: onDelete,
          disabled: isDeleting,
        },
      ]}
    />
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
            startIcon={
              <SvgColor width="20px" src="/assets/icons/ic_docs_single.svg" />
            }
            component="a"
            href="https://docs.futureagi.com/docs/agent-playground"
            target="_blank"
          >
            View Docs
          </Button>

          <LoadingButton
            variant="contained"
            color="primary"
            size="small"
            loading={isCreatingAgent}
            onClick={handleCreateAgent}
            startIcon={<SvgColor src="/assets/icons/ic_add.svg" width="20px" />}
          >
            Create Agent
          </LoadingButton>
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
