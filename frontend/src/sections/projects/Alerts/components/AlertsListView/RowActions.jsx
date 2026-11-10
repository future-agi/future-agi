import React from "react";
import SelectionBar from "src/components/selection-bar";
import { useAlertStore } from "../../store/useAlertStore";

export default function RowActions() {
  // hasUnMutedAlerts is for selected rows
  // currentPageHasMutedAlerts is for whole page selection "select all"
  const {
    selectedRows,
    hasUnMutedAlerts,
    currentPageHasMutedAlerts,
    handleOpenActionModal,
    selectedAll,
    excludingIds,
    totalRows,
    handleCancelSelection,
  } = useAlertStore();

  const count = selectedAll
    ? totalRows - excludingIds?.length
    : selectedRows.length;
  const isMute = selectedAll
    ? !currentPageHasMutedAlerts()
    : hasUnMutedAlerts();

  return (
    <SelectionBar
      count={count}
      onCancel={handleCancelSelection}
      actions={[
        {
          label: "Delete",
          icon: "solar:trash-bin-trash-linear",
          onClick: () => handleOpenActionModal("delete"),
        },
        {
          label: isMute ? "Mute" : "Unmute",
          icon: isMute ? "solar:bell-off-linear" : "solar:bell-linear",
          onClick: () => handleOpenActionModal(isMute ? "mute" : "unmute"),
        },
      ]}
    />
  );
}
