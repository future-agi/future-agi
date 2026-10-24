import { Box, Button, Typography, useTheme } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { useAuthContext } from "src/auth/hooks";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import CustomDialog from "../Common/CustomDialog/CustomDialog";
import { useRunExperimentStoreShallow } from "../states";

const ExperimentTabHeader = ({
  tabRef,
  experimentSearch,
  setExperimentSearch,
  rowSelected,
  setRowSelected,
  setCurrentTab,
}) => {
  const theme = useTheme();
  const [openDialog, setOpenDialog] = useState(null);
  const timeoutRef = useRef(null);
  const { role } = useAuthContext();
  const { initiateCreateMode } = useRunExperimentStoreShallow((state) => ({
    initiateCreateMode: state.initiateCreateMode,
  }));

  const handleButtonClick = (action) => {
    if (action === "Re-Run") {
      trackEvent(Events.expRowReRunClick);
    }
    if (action === "Cancel") {
      setRowSelected([]);
      if (tabRef.current) {
        tabRef.current.clearRowSelection();
      }
    } else {
      setOpenDialog(action);
    }
  };

  const refreshGrid = () => {
    if (tabRef.current) {
      tabRef.current.refreshExperimentTab();
    }
  };

  const mutation = useMutation({
    mutationFn: async (data) =>
      axios.post(endpoints.develop.experiment.rerun, data),
    onSuccess: () => {
      enqueueSnackbar("Re-Run has started for the selected experiments", {
        variant: "success",
      });
      closeDialog();
      timeoutRef.current = setTimeout(refreshGrid, 500);
    },
    onError: (error) => {
      timeoutRef.current = setTimeout(refreshGrid, 500);
      logger.error("Re-run error:", error);
    },
  });

  const handleRerun = () => {
    const data = { experiment_ids: rowSelected.map((item) => item.id) };
    trackEvent(Events.expRowReRunSuccess, {
      experiment_name: rowSelected.map((item) => item.name),
    });
    mutation.mutate(data);
  };

  const deleteMutation = useMutation({
    mutationFn: async (data) =>
      axios.delete(endpoints.develop.experiment.delete, { data }),
    onSuccess: () => {
      enqueueSnackbar("Experiment has been deleted successfully", {
        variant: "success",
      });
      closeDialog();
      if (tabRef.current) {
        tabRef.current.clearRowSelection();
      }
      refreshGrid();
    },
    onError: (error) => {
      logger.error("Delete error:", error);
    },
  });

  const handleDelete = () => {
    const data = { experiment_ids: rowSelected.map((item) => item.id) };
    trackEvent(Events.expRowDeleted, {
      [PropertyName.expRowDelete]: {
        dataset: rowSelected.map((item) => item.dataset),
        experimentId: rowSelected.map((item) => item.id),
      },
    });

    deleteMutation.mutate(data);
  };

  const closeDialog = () => {
    setOpenDialog(null);
  };

  const actions = useMemo(() => {
    return [
      {
        name: "Re-Run",
        disabled: !RolePermission.DATASETS[PERMISSIONS.UPDATE][role],
        color: "primary.main",
      },
      {
        name: "Delete",
        disabled: !RolePermission.DATASETS[PERMISSIONS.DELETE][role],
        color: "error.main",
      },
      { name: "Cancel", disabled: false, color: "text.primary" },
    ];
  }, [role]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "row",
        justifyContent: "space-between",
        paddingY: 1,
        paddingX: 1,
        paddingBottom: -1,
      }}
    >
      <FormSearchField
        size="small"
        placeholder="Search"
        searchQuery={experimentSearch}
        onChange={(e) => setExperimentSearch(e.target.value)}
        sx={{ minWidth: "360px" }}
      />
      <ShowComponent condition={rowSelected?.length === 0}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            gap: 1,
          }}
        >
          {/* <Button
            size="medium"
            variant="outlined"
            color={"primary"}
            startIcon={<SvgColor src="/assets/icons/navbar/ic_evaluate.svg" />}
          >
            Rerun all
          </Button> */}
          <Button
            size="medium"
            variant="contained"
            color={"primary"}
            startIcon={<Iconify icon="solar:add-linear" />}
            onClick={() => {
              setCurrentTab("data");
              timeoutRef.current = setTimeout(() => {
                initiateCreateMode();
              }, 700);
            }}
          >
            Add Experiments
          </Button>

          {/* <Button
            size="medium"
            variant="outlined"
            color={"error"}
            startIcon={<SvgColor src="/assets/icons/ic_stop.svg" />}
            disabled={!isAnyExperimentRunning}
          >
            Stop All
          </Button> */}
        </Box>
      </ShowComponent>
      <ShowComponent condition={rowSelected?.length > 0}>
        {rowSelected.length > 0 && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-around",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              padding: `${theme.spacing(0.5)} ${theme.spacing(2)}`,
              gap: theme.spacing(1),
              marginRight: theme.spacing(0.625),

              marginLeft: "auto",
            }}
          >
            <Box
              sx={{
                color: theme.palette.text,
                marginRight: theme.spacing(1.5),
                paddingRight: theme.spacing(3),
                borderRight: "1px solid",
                borderColor: "divider",
              }}
            >
              {`${rowSelected.length} selected`}
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0 }}>
              {actions.map((action, index) => (
                <Box
                  key={action.name}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    borderRight:
                      index < actions.length - 1 ? "1px solid divider" : "none",
                  }}
                >
                  <Button
                    variant="text"
                    sx={{
                      color: action.color,
                      padding: `${theme.spacing(0.75)} ${theme.spacing(1.25)}`,
                    }}
                    disabled={action.disabled}
                    onClick={() => handleButtonClick(action.name)}
                    startIcon={
                      action.name === "Re-Run" ? (
                        <SvgColor src="/assets/icons/navbar/ic_evaluate.svg" />
                      ) : action.name === "Delete" ? (
                        <SvgColor src="/assets/icons/ic_delete.svg" />
                      ) : null
                    }
                  >
                    {action.name}
                  </Button>
                </Box>
              ))}
            </Box>
          </Box>
        )}
      </ShowComponent>
      {/* Delete Confirmation Dialog */}
      <CustomDialog
        open={openDialog === "Delete"}
        onClose={closeDialog}
        title={`Delete Experiment${rowSelected?.length > 1 ? "s" : ""}`}
        actionButton="Delete"
        onClickAction={handleDelete}
        // preTitleIcon="solar:trash-bin-trash-bold"
        color="error"
      >
        <Typography style={{ color: "text.secondary" }}>
          {`Are you sure you want to delete the selected ${rowSelected?.length} experiment${rowSelected?.length > 1 ? "s" : ""}?`}
        </Typography>
      </CustomDialog>

      {/* Re-Run Confirmation Dialog */}
      <CustomDialog
        open={openDialog === "Re-Run"}
        onClose={closeDialog}
        title={`Re-Run Experiment${rowSelected?.length > 1 ? "s" : ""}`}
        actionButton="Re-Run"
        onClickAction={handleRerun}
        // preTitleIcon="token:rune"
        color="primary"
      >
        <Typography style={{ color: "text.secondary" }}>
          {`Are you sure you want to re-run the selected ${rowSelected?.length} experiment${rowSelected?.length > 1 ? "s" : ""}? Existing run data will be overwritten`}
        </Typography>
      </CustomDialog>
    </Box>
  );
};

export default ExperimentTabHeader;
ExperimentTabHeader.propTypes = {
  experimentSearch: PropTypes.string,
  setExperimentSearch: PropTypes.func,
  rowSelected: PropTypes.array,
  setRowSelected: PropTypes.func,
  tabRef: PropTypes.object,
  setCurrentTab: PropTypes.func,
};
