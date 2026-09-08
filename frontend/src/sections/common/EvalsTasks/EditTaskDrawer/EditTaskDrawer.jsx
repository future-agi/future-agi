import React, { useRef } from "react";
import { ShowComponent } from "src/components/show";
import DetailsEdit from "./DetailsEdit";
import PropTypes from "prop-types";
import { useGetTaskData } from "../common";
import { Alert, Box, Button, CircularProgress, Drawer } from "@mui/material";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

const EditTaskDrawer = (props) => {
  const setVisibleSectionRef = useRef(null);
  const handleClose = () => {
    setVisibleSectionRef.current = "list";
    props?.onClose();
  };
  return <EditTaskDrawerChild {...props} onClose={handleClose} />;
};

EditTaskDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedRow: PropTypes.object,
  refreshGrid: PropTypes.func,
  observeId: PropTypes.string,
  isEdit: PropTypes.bool,
  isView: PropTypes.bool,
};

const EditTaskDrawerChild = ({
  selectedRow,
  refreshGrid,
  onClose,
  isView = false,
  isEdit = false,
  open,
}) => {
  const taskId = selectedRow?.id;

  const {
    data: taskDetails,
    isLoading,
    isFetching,
    isError,
    error,
    refetch,
  } = useGetTaskData(taskId, {
    enabled: !!taskId,
  });

  if (!taskDetails && (isLoading || isError)) {
    return (
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        PaperProps={{ sx: { width: { xs: "100%", sm: 480 }, p: 2 } }}
      >
        {isLoading ? (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
            }}
          >
            <CircularProgress size={28} />
          </Box>
        ) : (
          <Alert
            severity="error"
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => refetch()}
                disabled={isFetching}
              >
                Retry
              </Button>
            }
          >
            {getSafeActionErrorMessage(
              error,
              "Task details could not be loaded.",
            )}
          </Alert>
        )}
      </Drawer>
    );
  }

  return (
    <>
      {isError && taskDetails && (
        <Alert
          severity="error"
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              Retry
            </Button>
          }
          sx={{
            position: "fixed",
            top: 16,
            right: 16,
            zIndex: (theme) => theme.zIndex.modal + 1,
            maxWidth: 440,
          }}
        >
          {getSafeActionErrorMessage(
            error,
            "Task details could not be refreshed.",
          )}{" "}
          Existing task details are still shown.
        </Alert>
      )}
      <ShowComponent condition={!!taskDetails}>
        <DetailsEdit
          loading={isLoading}
          isEdit={isEdit}
          title={selectedRow?.name}
          isView={isView}
          observeId={taskDetails?.project_id}
          taskDetails={taskDetails}
          selectedRow={selectedRow}
          onClose={onClose}
          refreshGrid={refreshGrid}
          open={open}
        />
      </ShowComponent>
    </>
  );
};

EditTaskDrawerChild.propTypes = {
  selectedRow: PropTypes.object,
  refreshGrid: PropTypes.func,
  onClose: PropTypes.func,
  isView: PropTypes.bool,
  isEdit: PropTypes.bool,
  open: PropTypes.bool,
};

export default EditTaskDrawer;
