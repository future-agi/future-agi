import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import SvgColor from "src/components/svg-color";
import { getActionButtonConfig, getActionTitle } from "../common";
import { LoadingButton } from "@mui/lab";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAlertStore } from "../store/useAlertStore";

const RowItem = ({ name, onRemove, disabled }) => {
  return (
    <Stack
      flexDirection={"row"}
      justifyContent={"space-between"}
      alignItems={"center"}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        padding: 1.5,
      }}
    >
      <Typography
        fontWeight={"fontWeightMedium"}
        variant="s1"
        color={"text.primary"}
      >
        {name}
      </Typography>
      <IconButton disabled={disabled} size="small" onClick={onRemove}>
        <SvgColor
          sx={{
            height: 16,
            width: 16,
            bgcolor: "text.primary",
          }}
          src="/assets/icons/ic_close.svg"
        />
      </IconButton>
    </Stack>
  );
};

RowItem.propTypes = {
  name: PropTypes.string,
  onRemove: PropTypes.func,
  disabled: PropTypes.bool,
};

export default function ConfirmAlertsDialog() {
  const {
    actionModal,
    handleCloseActionModal,
    selectedRows,
    handleRemoveRow,
    deleteAlerts,
    muteAlerts,
    isDeletingAlerts,
    isMutingAlerts,
    selectedAll,
    excludingIds,
    totalRows,
  } = useAlertStore();

  const { cancel, action } = getActionButtonConfig(
    actionModal.type,
    selectedRows,
  );

  const trackMuteEvent = (ids, toggle) => {
    trackEvent(Events.alertMuteClicked, {
      [PropertyName.list]: ids,
      [PropertyName.toggle]: toggle,
      [PropertyName.source]: "alert_homepage",
    });
  };

  const handleAction = () => {
    if (selectedAll) {
      if (actionModal.type === "delete") {
        deleteAlerts({ select_all: true, exclude_ids: excludingIds });
      } else if (actionModal.type === "mute") {
        muteAlerts({
          select_all: true,
          exclude_ids: excludingIds,
          is_mute: true,
        });
        trackMuteEvent([], "Mute");
      } else if (actionModal.type === "unmute") {
        muteAlerts({
          select_all: true,
          exclude_ids: excludingIds,
          is_mute: false,
        });
        trackMuteEvent([], "Unmute");
      }
    } else if (selectedRows?.length > 0) {
      const ids = selectedRows.map((row) => row.id);

      if (actionModal.type === "delete") {
        deleteAlerts({ ids });
      } else if (actionModal.type === "mute") {
        muteAlerts({ ids, is_mute: true });
        trackMuteEvent(ids, "Mute");
      } else if (actionModal.type === "unmute") {
        muteAlerts({ ids, is_mute: false });
        trackMuteEvent(ids, "Unmute");
      }
    }
  };

  const actionTitle = useMemo(
    () =>
      getActionTitle(
        actionModal.type,
        selectedRows.length,
        selectedAll ? totalRows - excludingIds.length : undefined,
      ),
    [
      actionModal.type,
      excludingIds.length,
      selectedAll,
      selectedRows.length,
      totalRows,
    ],
  );

  return (
    <Dialog
      PaperProps={{
        sx: {
          p: 2,
          maxWidth: "540px",
          minWidth: "540px",
        },
      }}
      open={actionModal.state}
      onClose={handleCloseActionModal}
    >
      <DialogTitle
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
          padding: 0,
          gap: 4,
        }}
      >
        <Typography
          variant="m3"
          color={"text.primary"}
          fontWeight={"fontWeightBold"}
        >
          {actionTitle}
        </Typography>
        <IconButton
          disabled={isDeletingAlerts || isMutingAlerts}
          onClick={handleCloseActionModal}
        >
          <SvgColor
            sx={{
              bgcolor: "text.primary",
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: 0,
          mt: 1.5,
        }}
      >
        <Stack gap={1.5}>
          {Array.isArray(selectedRows) &&
            selectedRows?.length <= 20 &&
            selectedRows?.map((row, index) => (
              <RowItem
                key={index}
                name={row?.name}
                disabled={isDeletingAlerts || isMutingAlerts}
                onRemove={() => handleRemoveRow(row?.id)}
              />
            ))}
        </Stack>
      </DialogContent>
      <DialogActions
        sx={{
          p: 0,
          mt: 1.5,
        }}
      >
        <Button
          size="small"
          onClick={handleCloseActionModal}
          variant="outlined"
          disabled={isDeletingAlerts || isMutingAlerts}
        >
          {cancel?.title}
        </Button>
        <LoadingButton
          loading={isDeletingAlerts || isMutingAlerts}
          onClick={handleAction}
          color={action?.color}
          size="small"
          variant="contained"
          data-alert-confirm-action={actionModal.type || ""}
          disabled={isDeletingAlerts || isMutingAlerts}
        >
          {action?.title}
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}
