import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import React, { useState } from "react";
import { useCreateWorkspaceModal } from "../states";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useForm } from "react-hook-form";
import { CreateWorkspaceValidation } from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useCreateWorkspace } from "src/api/workspaces/create";
import { LoadingButton } from "@mui/lab";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import WorkspaceSuccess from "./WorkspaceSuccess";

const CreateWorkspaceForm = () => {
  const { setOpen } = useCreateWorkspaceModal();
  const [successData, setSuccessData] = useState(null);
  const { control, handleSubmit } = useForm({
    defaultValues: {
      name: "",
    },
    resolver: zodResolver(CreateWorkspaceValidation),
  });

  const { mutate: createWorkspace, isPending: isLoading } = useCreateWorkspace({
    onSuccess: (response) => {
      setSuccessData(response?.data?.result);
      trackEvent(Events.workspaceCreateRequestSubmitted, {
        [PropertyName.click]: "click",
      });
    },
  });

  const onSubmit = (data) => {
    createWorkspace(data);
  };

  if (successData) {
    return (
      <WorkspaceSuccess
        workspaceData={successData}
        onClose={() => {
          setSuccessData(null);
          setOpen(false);
        }}
      />
    );
  }
  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <DialogTitle
        sx={{
          padding: 2,
        }}
      >
        <Box
          sx={{
            borderBottom: "1px solid",
            borderColor: "divider",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          Create new workspace
          <IconButton size="small" onClick={() => setOpen(false)}>
            <Iconify icon="akar-icons:cross" sx={{ color: "text.primary" }} />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: 2,
          paddingY: 0,
          gap: 1,
        }}
      >
        <Box
          sx={{ paddingY: 1, display: "flex", flexDirection: "column", gap: 2 }}
        >
          <FormTextFieldV2
            control={control}
            fieldName="name"
            label="Workspace name"
            required
            size="small"
            placeholder="Workspace name"
          />
        </Box>
      </DialogContent>
      <DialogActions sx={{ padding: 2, flexDirection: "column", gap: 1 }}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ width: "100%", textAlign: "left" }}
        >
          You will be the admin of this workspace.
        </Typography>
        <LoadingButton
          loading={isLoading}
          variant="contained"
          color="primary"
          fullWidth
          type="submit"
        >
          Create workspace
        </LoadingButton>
      </DialogActions>
    </form>
  );
};

const CreateWorkspaceModal = () => {
  const { open, setOpen } = useCreateWorkspaceModal();
  return (
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
      <CreateWorkspaceForm />
    </Dialog>
  );
};

export default CreateWorkspaceModal;
