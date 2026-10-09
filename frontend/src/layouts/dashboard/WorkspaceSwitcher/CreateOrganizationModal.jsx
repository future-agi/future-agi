import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import React from "react";
import { useCreateOrganizationModal } from "../states";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { isEnterpriseGateError } from "src/hooks/use-credit-exhaustion";
import { useOrganization } from "src/contexts/OrganizationContext";
import { z } from "zod";

const CreateOrganizationValidation = z.object({
  name: z.string().min(1, "Organization name is required"),
  display_name: z.string().optional(),
});

const CreateOrganizationForm = () => {
  const { setOpen } = useCreateOrganizationModal();
  const queryClient = useQueryClient();
  const { switchOrganization } = useOrganization();

  const { control, handleSubmit } = useForm({
    defaultValues: {
      name: "",
      display_name: "",
    },
    resolver: zodResolver(CreateOrganizationValidation),
  });

  const { mutate: createOrganization, isPending: isLoading } = useMutation({
    mutationFn: (data) => axios.post(endpoints.organizations.create, data),
    onSuccess: (response) => {
      const result = response?.data?.result || response?.data || {};
      const orgId = result.organization?.id;

      enqueueSnackbar("Organization created successfully", {
        variant: "success",
      });

      queryClient.invalidateQueries({ queryKey: ["organizations-list"] });
      setOpen(false);

      // Switch to the new org
      if (orgId) {
        switchOrganization(orgId);
      }
    },
    onError: (error) => {
      // A 2nd organization on Community: the Enterprise gate dialog explains.
      if (isEnterpriseGateError(error)) {
        setOpen(false);
        return;
      }
      enqueueSnackbar(
        error?.response?.data?.result ||
          error?.message ||
          "Failed to create organization",
        { variant: "error" },
      );
    },
  });

  const onSubmit = (data) => {
    createOrganization({
      name: data.name,
      display_name: data.display_name || data.name,
    });
  };

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
          Create new organization
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
            label="Organization name"
            required
            size="small"
            placeholder="e.g. Acme Corp"
          />
          <FormTextFieldV2
            control={control}
            fieldName="display_name"
            label="Display name (optional)"
            size="small"
            placeholder="e.g. Acme Corporation"
          />
          <Typography variant="caption" color="text.secondary">
            You will be the Owner of this organization. A default workspace will
            be created automatically.
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <LoadingButton
          loading={isLoading}
          variant="contained"
          color="primary"
          fullWidth
          type="submit"
        >
          Create organization
        </LoadingButton>
      </DialogActions>
    </form>
  );
};

const CreateOrganizationModal = () => {
  const { open, setOpen } = useCreateOrganizationModal();
  return (
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
      <CreateOrganizationForm />
    </Dialog>
  );
};

export default CreateOrganizationModal;
