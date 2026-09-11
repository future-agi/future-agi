import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useAgentConfigForm, useAgentSubmit } from "../useAgentConfigForm";
import { createAgentDefinitionSchema } from "../helper";
import KeyConfiguration from "./KeyConfiguration";
import { LoadingButton } from "@mui/lab";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";

const UpdateKeysDialogChild = ({
  agentDetails,
  onClose,
  onComplete,
  agentDefinitionId,
}) => {
  const defaultValue = useMemo(() => {
    if (!agentDetails) return null;
    return {
      ...agentDetails,
      api_key: agentDetails.api_key,
      configuration_snapshot: {
        ...agentDetails.configuration_snapshot,
        commit_message: "Updated keys",
      },
    };
  }, [agentDetails]);
  const { control, handleSubmit, reset, formState } = useAgentConfigForm(
    createAgentDefinitionSchema({ keysRequired: true, agentDefinitionId }),
    defaultValue,
  );

  const queryClient = useQueryClient();
  const { onSubmit, isPending } = useAgentSubmit({
    agentDefinitionId,
    reset,
    queryClient,
    enqueueSnackbar,
    setError: () => {},
    onClose: onComplete,
    setSelectedVersion: () => {},
  });

  const { errors } = formState;

  return (
    <>
      <DialogTitle sx={{ padding: 2 }}>
        Update Keys for {agentDetails?.configuration_snapshot?.agent_name}
      </DialogTitle>
      <DialogContent sx={{ padding: 2 }}>
        <Box
          sx={{ display: "flex", flexDirection: "column", gap: 2, paddingY: 1 }}
        >
          <KeyConfiguration control={control} errors={errors} required={true} />
        </Box>
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <Button variant="outlined" color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          onClick={handleSubmit(onSubmit)}
          loading={isPending}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </>
  );
};

UpdateKeysDialogChild.propTypes = {
  agentDetails: PropTypes.object,
  onClose: PropTypes.func,
  onComplete: PropTypes.func,
  agentDefinitionId: PropTypes.string,
};

const UpdateKeysDialog = ({
  open,
  onClose,
  agentDetails,
  onComplete,
  agentDefinitionId,
}) => {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <UpdateKeysDialogChild
        agentDetails={agentDetails}
        onClose={onClose}
        onComplete={onComplete}
        agentDefinitionId={agentDefinitionId}
      />
    </Dialog>
  );
};

UpdateKeysDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agentDetails: PropTypes.object,
  onComplete: PropTypes.func,
  agentDefinitionId: PropTypes.string,
};

export default UpdateKeysDialog;
