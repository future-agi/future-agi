import React from "react";
import PropTypes from "prop-types";
import { Box, Drawer, IconButton, Typography, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";
import { useForm } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import { LoadingButton } from "@mui/lab";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useSnackbar } from "src/components/snackbar";
import { generateNameFromEmail } from "src/sections/auth/jwt/common";

import { InviteMemberValidation } from "./validation";

const InviteUserDrawerForm = ({ onClose }) => {
  const { control, handleSubmit, setError } = useForm({
    defaultValues: {
      email: "",
      organizationRole: "",
    },
    resolver: zodResolver(InviteMemberValidation),
  });

  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();

  const { mutate, isPending } = useMutation({
    meta: { errorHandled: true },
    mutationFn: (data) =>
      axios.post(endpoints.settings.teams.inviteMember, data),
    onSuccess: (response) => {
      const result = response?.data?.result;

      // Check if there are errors in the response
      if (result?.errors && result.errors.length > 0) {
        // Display all errors
        result.errors.forEach((error) => {
          enqueueSnackbar(error.error, { variant: "error" });
        });
        return; // Don't close drawer or show success
      }

      const members = result?.created_members || [];
      const warnings = members.filter((member) => member.invite_email_warning);
      if (warnings.length > 0) {
        warnings.forEach((member) => {
          enqueueSnackbar(`${member.email}: ${member.invite_email_warning}`, {
            variant: "warning",
            persist: true,
          });
        });
      } else if (members.length > 0) {
        enqueueSnackbar(`Successfully invited ${members.length} user(s)`, {
          variant: "success",
        });
      }

      onClose();
      queryClient.invalidateQueries({ queryKey: ["member-list"], type: "all" });
    },
    onError: (e, variables) => {
      const result = e?.result;
      const submittedEmail = variables?.members?.[0]?.email
        ?.trim()
        .toLowerCase();
      const memberError = Array.isArray(result?.errors)
        ? result.errors.find(
            (error) =>
              typeof error?.email === "string" &&
              error.email.trim().toLowerCase() === submittedEmail,
          )
        : null;
      const errorMessage =
        [
          memberError?.error,
          result?.message,
          typeof result === "string" ? result : null,
          e?.message,
        ].find((message) => typeof message === "string" && message.trim()) ||
        "Failed to send invite";
      enqueueSnackbar(errorMessage, { variant: "error" });
      setError("email", { message: errorMessage });
    },
  });

  const onFormSubmit = (formValues) =>
    mutate({
      members: [
        {
          email: formValues.email,
          name: generateNameFromEmail(formValues.email),
          organization_role: formValues.organizationRole,
        },
      ],
    });

  return (
    <Box
      sx={{
        padding: 2,
        flex: 1,
        display: "flex",
        flexDirection: "column",
        zIndex: 21,
        gap: 2,
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography variant="subtitle1" color="text.disabled">
          Invite User
        </Typography>
        <IconButton onClick={() => onClose()}>
          <Iconify icon="mingcute:close-line" />
        </IconButton>
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100%",
        }}
      >
        <FormTextFieldV2
          control={control}
          fieldName="email"
          placeholder="Enter email"
          size="small"
          label="User Email"
        />
        <FormSelectField
          control={control}
          options={[
            { label: "Owner", value: "Owner" },
            { label: "Member", value: "Member" },
          ]}
          fieldName="organizationRole"
          label="User Role"
          size="small"
        />
        <Box sx={{ flexGrow: 1 }} />
        <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
          <LoadingButton
            variant="contained"
            color="primary"
            onClick={handleSubmit(onFormSubmit)}
            loading={isPending}
          >
            Invite User
          </LoadingButton>
        </Box>
      </Box>
    </Box>
  );
};

InviteUserDrawerForm.propTypes = {
  onClose: PropTypes.func,
};

const InviteUserDrawer = (props) => {
  const theme = useTheme();
  const { open, onClose } = props;
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "80vh",
          width: "550px",
          position: "fixed",
          zIndex: 9999,
          top: "10%",
          right: 30,
          borderRadius: "10px",
          backgroundColor: "background.paper",
          "& .MuiDrawer-paper": {
            boxShadow: theme.shadows[15],
          },
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <InviteUserDrawerForm onClose={onClose} />
    </Drawer>
  );
};

InviteUserDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default InviteUserDrawer;
