import { Box, Button, Divider, Typography } from "@mui/material";
import React from "react";
import { styled } from "@mui/system";
import SvgColor from "src/components/svg-color";
import { trackEvent } from "src/utils/Mixpanel";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { useRefreshScenarioGrid } from "./useRefreshScenarioGrid";
import { useDevelopSelectedRowsStoreShallow } from "src/sections/develop-detail/states";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName } from "src/utils/Mixpanel";
import { enqueueSnackbar } from "src/components/snackbar";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";
import DeleteRowAction from "src/sections/develop-detail/DataTab/Delete/DeleteRowAction";
import { useMemo } from "react";
import PropTypes from "prop-types";

const StyledBox = styled(Box)(({ theme }) => ({
  gap: "12px",
  display: "flex",
  alignItems: "center",
  paddingX: "16px",
  borderRadius: theme.shape.borderRadius,
}));

const StyledTypography = styled(Typography)(({ theme }) => ({
  color: theme.palette.primary.main,
  fontWeight: theme.typography["fontWeightMedium"], // "500",
  ...theme.typography["s1"],
}));

const StyledButton = styled(Button)(({ theme }) => ({
  color: theme.palette.text.primary,
  fontWeight: theme.typography["fontWeightRegular"], // "500",
  ...theme.typography["s1"],
  "&:hover": {
    backgroundColor: theme.palette.action.hover,
  },
}));

const ScenarioDetailSelectionView = ({ dataset }) => {
  const [isDelete, setIsDelete] = useState(false);

  const { scenarioId } = useParams();
  const refreshGrid = useRefreshScenarioGrid(scenarioId);
  const { selectAll, toggledNodes, resetSelectedRows } =
    useDevelopSelectedRowsStoreShallow((s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
      resetSelectedRows: s.resetSelectedRows,
    }));

  const { role } = useAuthContext();

  const { gridApi } = useDevelopDetailContext();

  const { mutate: onDeleteDatasetRow, isPending: isDeleting } = useMutation({
    mutationFn: () => {
      const selectedIds = toggledNodes;

      return axios.delete(endpoints.develop.deleteDatasetRow(dataset), {
        data: { row_ids: selectedIds, selected_all_rows: selectAll },
      });
    },
    onSuccess: () => {
      const selectedIds = toggledNodes;
      trackEvent(Events.deleteRowSuccessful, {
        [PropertyName.deleteRow]: {
          selectedRow: selectedIds,
        },
      });
      setIsDelete(false);
      unCheckedHandler();
      refreshGrid();
      enqueueSnackbar("Rows deleted successfully", {
        variant: "success",
      });
    },
  });

  const unCheckedHandler = () => {
    resetSelectedRows();
    gridApi.current.deselectAll();
  };

  const selectedCount = useMemo(() => {
    const context = gridApi.current.getGridOption("context");
    if (selectAll) {
      return context.totalRowCount - toggledNodes.length;
    } else {
      return toggledNodes.length;
    }
  }, [toggledNodes, selectAll, gridApi]);

  return (
    <StyledBox>
      <Box sx={{ display: "flex", alignItems: "center" }}>
        <StyledTypography>{selectedCount} Selected</StyledTypography>
      </Box>

      <Divider orientation="vertical" flexItem />

      <StyledButton
        variant="text"
        size="small"
        disabled={!RolePermission.DATASETS[PERMISSIONS.DELETE][role]}
        startIcon={
          <SvgColor
            src={`/assets/icons/components/ic_delete.svg`}
            sx={{ width: "20px", height: "20px", color: "text.disabled" }}
          />
        }
        onClick={() => {
          trackEvent(Events.deleteRowClicked);
          setIsDelete(!isDelete);
        }}
      >
        Delete
      </StyledButton>
      <Divider orientation="vertical" flexItem />

      <StyledButton
        variant="text"
        size="small"
        onClick={() => unCheckedHandler()}
        sx={{
          padding: 0,
          minWidth: 0,
          marginRight: 2,
          color: "text.primary",
        }}
      >
        Cancel
      </StyledButton>

      {isDelete && (
        <CustomDialog
          title={`Delete Row${selectedCount > 1 ? `s` : ""}`}
          color="error"
          actionButton="Delete"
          open={isDelete}
          preTitleIcon="solar:trash-bin-trash-bold"
          onClose={() => setIsDelete((prev) => !prev)}
          loading={isDeleting}
          onClickAction={() => onDeleteDatasetRow()}
        >
          <DeleteRowAction selectedCount={selectedCount} />
        </CustomDialog>
      )}
    </StyledBox>
  );
};

ScenarioDetailSelectionView.propTypes = {
  dataset: PropTypes.string.isRequired,
};

export default ScenarioDetailSelectionView;
