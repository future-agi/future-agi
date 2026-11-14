import SelectionBar from "src/components/selection-bar";
import React from "react";
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
    <>
      <SelectionBar
        count={selectedCount}
        onCancel={() => unCheckedHandler()}
        actions={[
          {
            label: "Delete",
            icon: "solar:trash-bin-trash-linear",
            disabled: !RolePermission.DATASETS[PERMISSIONS.DELETE][role],
            onClick: () => {
              trackEvent(Events.deleteRowClicked);
              setIsDelete(!isDelete);
            },
          },
        ]}
      />

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
    </>
  );
};

ScenarioDetailSelectionView.propTypes = {
  dataset: PropTypes.string.isRequired,
};

export default ScenarioDetailSelectionView;
