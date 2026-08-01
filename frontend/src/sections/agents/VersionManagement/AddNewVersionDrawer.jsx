import React, { useEffect, useState } from "react";
import {
  Drawer,
  Box,
  Typography,
  IconButton,
  Button,
  Alert,
  Link,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import EditAgentDetails from "../AgentConfiguration/EditAgentDetails";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { ConfirmDialog } from "src/components/custom-dialog";
import { createAgentDefinitionSchema } from "../helper";
import { useQueryClient } from "@tanstack/react-query";
import { useSnackbar } from "notistack";
import { useAgentConfigForm, useAgentSubmit } from "../useAgentConfigForm";
import { useParams } from "react-router";
import { LoadingButton } from "@mui/lab";
import { useAgentDetailsStore } from "../store/agentDetailsStore";
import { handleOnDocsClicked } from "src/utils/Mixpanel";

const AddNewVersionDrawer = ({ open, onClose, latestVersion }) => {
  const { agentDefinitionId } = useParams();
  const { agentDetails, setSelectedVersion, resetAgentDetails } =
    useAgentDetailsStore();
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();

  const {
    control,
    handleSubmit,
    reset,
    watch,
    formState,
    setValue,
    getValues,
    trigger,
  } = useAgentConfigForm(createAgentDefinitionSchema({ agentDefinitionId }), agentDetails);

  useEffect(() => {
    return () => resetAgentDetails();
  }, [resetAgentDetails]);

  const { errors, isSubmitting, isValid, isDirty } = formState;

  const [error, setError] = useState("");
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);

  const commitMessage = watch("commitMessage");

  const resetFormAndClose = () => {
    reset();
    setError("");
    onClose();
  };

  const handleDrawerClose = () => {
    if (isDirty) {
      setConfirmDialogOpen(true);
    } else {
      resetFormAndClose();
    }
  };

  const { onSubmit } = useAgentSubmit({
    agentDefinitionId,
    reset,
    queryClient,
    enqueueSnackbar,
    setError,
    setSelectedVersion,
    onClose: resetFormAndClose,
  });

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        variant="temporary"
        onClose={handleDrawerClose}
        PaperProps={{
          sx: {
            height: "100vh",
            position: "fixed",
            display: "flex",
            flexDirection: "column",
            borderRadius: "10px 0 0 10px",
            backgroundColor: "background.paper",
            width: "50vw",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        {/* Header */}
        <Box
          display="flex"
          justifyContent="space-between"
          alignItems="flex-start"
          p={2}
          borderBottom="1px solid"
          borderColor="divider"
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              Create new version
            </Typography>
            {/* <Typography
              typography="s2_1"
              color="text.disabled"
              fontWeight={"fontWeightRegular"}
            >
              {`Create a new version of ${agentName}`}
            </Typography> */}
            <Typography
              typography="s2_1"
              color="text.secondary"
              fontWeight={"fontWeightRegular"}
            >
              Edit this configuration to create as a new version{" "}
              <Link
                onClick={() => handleOnDocsClicked("agent_definition_page")}
                href="https://docs.futureagi.com/docs/simulation/concepts/agent-definition"
                color="blue.500"
                target="_blank"
                rel="noopener noreferrer"
                fontWeight="fontWeightMedium"
                fontSize="14px"
                sx={{ textDecoration: "underline", fontSize: "13px" }}
              >
                Learn more
              </Link>{" "}
            </Typography>
          </Box>

          <IconButton onClick={handleDrawerClose}>
            <Iconify icon="mingcute:close-line" color="text.primary" />
          </IconButton>
        </Box>

        {/* Content (scrollable) */}
        <Box
          flex={1}
          overflow="auto"
          p={2}
          display="flex"
          flexDirection="column"
          gap={2}
          sx={{
            "&::-webkit-scrollbar": {
              width: "6px",
            },
            "&::-webkit-scrollbar-thumb": {
              backgroundColor: "rgba(0, 0, 0, 0.3)",
              borderRadius: "3px",
            },
            "&::-webkit-scrollbar-track": {
              backgroundColor: "transparent",
            },
          }}
        >
          <Box>
            <FormTextFieldV2
              control={control}
              required
              fieldName="commitMessage"
              placeholder="Describe the changes and improvements in this version..."
              size="small"
              multiline
              label="What's changing in this version?"
              rows={3}
              fullWidth
              error={!!errors.commitMessage}
              helperText={errors.commitMessage?.message}
              sx={{
                "& .MuiInputLabel-root": {
                  fontWeight: 500,
                },
              }}
            />
          </Box>
          {error && <Alert severity="error">{error}</Alert>}
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Agent Configuration
          </Typography>
          <EditAgentDetails
            control={control}
            errors={errors}
            setValue={setValue}
            getValues={getValues}
            trigger={trigger}
          />
        </Box>
        <Box
          display="flex"
          justifyContent="flex-end"
          gap={1}
          p={2}
          borderTop="1px solid"
          borderColor="background.neutral"
        >
          <LoadingButton
            variant="contained"
            size="small"
            fullWidth
            color="primary"
            onClick={handleSubmit(onSubmit)}
            loading={isSubmitting}
            disabled={!isValid || !commitMessage}
            sx={{ borderRadius: "4px", height: "34px" }}
          >
            {`Create version v${latestVersion + 1}`}
          </LoadingButton>
        </Box>
      </Drawer>
      <ConfirmDialog
        open={confirmDialogOpen}
        onClose={() => setConfirmDialogOpen(false)}
        title="Discard changes?"
        content="You have unsaved changes. Are you sure you want to close this form?"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            sx={{ paddingX: "24px" }}
            onClick={() => {
              setConfirmDialogOpen(false);
              resetFormAndClose();
            }}
          >
            Confirm
          </Button>
        }
      />
    </>
  );
};

AddNewVersionDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  latestVersion: PropTypes.number,
};

export default AddNewVersionDrawer;
