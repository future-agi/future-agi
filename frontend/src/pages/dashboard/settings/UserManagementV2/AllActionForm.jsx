import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  alpha,
  Box,
  Button,
  Typography,
  useTheme,
  Autocomplete,
  TextField,
  Chip,
} from "@mui/material";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import ActionForm from "./ActionForm";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import { orgRoleOptions, wsRoleOptions, LEVELS } from "./constant";
import { ShowComponent } from "src/components/show";
import { useAuthContext } from "src/auth/hooks";
import ChipsInput from "src/components/ChipsInput/ChipsInput";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useUserManagementStore } from "./UserManagementStore";

const isValidEmail = (email) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
const formatItem = (email) => email.trim().toLowerCase();
const emailRules = {
  required: "At least one email is required",
  validate: (value) => value.length > 0 || "Please add at least one email",
};

/**
 * Build Zod schema for invite form.
 */
const inviteFormSchema = (existingEmails) =>
  zodResolver(
    z
      .object({
        emails: z
          .array(z.string().email("Invalid email address"))
          .nonempty("At least one email is required")
          .refine(
            (emails) => {
              const duplicates = emails.filter((email) =>
                existingEmails.includes(email),
              );
              return duplicates.length === 0;
            },
            (emails) => {
              const duplicates = emails.filter((email) =>
                existingEmails.includes(email),
              );
              return {
                message:
                  duplicates?.length === 1
                    ? `The user ${duplicates[0]} has already been invited`
                    : `These users ${duplicates.join(", ")} have already been invited`,
              };
            },
          ),
        orgLevel: z.number({ required_error: "Organization role is required" }),
        wsLevel: z.number().optional().nullable(),
        workspaceIds: z.array(z.string()).optional(),
      })
      .refine(
        (data) => {
          // Admin/Owner auto-access all workspaces, no selection needed
          if (data.orgLevel >= LEVELS.ADMIN) return true;
          // Member/Viewer must have at least one workspace
          return data.workspaceIds && data.workspaceIds.length > 0;
        },
        {
          message: "Please select at least one workspace",
          path: ["workspaceIds"],
        },
      ),
  );

/**
 * Build Zod schema for edit role form.
 */
const editRoleSchema = zodResolver(
  z.object({
    orgLevel: z.number().optional().nullable(),
    wsLevel: z.number().optional().nullable(),
    workspaceIds: z.array(z.string()).optional(),
    wsLevel_edit: z.number().optional().nullable(),
  }),
);

const AllActionForm = ({
  openActionForm,
  onClose,
  userData,
  gridApi,
  workspaceId,
}) => {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const { user, orgLevel: actorOrgLevel } = useAuthContext();
  const { usersList } = useUserManagementStore();
  const existingEmails = usersList.map((u) => u.email);

  const isOwner =
    actorOrgLevel != null
      ? actorOrgLevel >= LEVELS.OWNER
      : user?.organization_role === "Owner";
  const isAdmin = actorOrgLevel != null ? actorOrgLevel >= LEVELS.ADMIN : false;

  // Filter org role options based on actor's level
  const filteredOrgRoleOptions = useMemo(() => {
    if (isOwner) return orgRoleOptions; // Owner sees all 4
    if (isAdmin) {
      // Admin sees Admin + Member + Viewer (equal or below own level)
      return orgRoleOptions.filter((opt) => opt.value <= LEVELS.ADMIN);
    }
    // WS Admin: org role is auto-set to Viewer, no dropdown needed
    return [];
  }, [isOwner, isAdmin]);

  // Fetch workspace list for the org
  const { data: workspacesData } = useQuery({
    queryKey: ["workspace-list-for-invite"],
    queryFn: () => axios.get(endpoints.workspace.workspaceList),
    staleTime: 30000,
  });
  const allWorkspaces = useMemo(() => {
    const list =
      workspacesData?.data?.result?.results ||
      workspacesData?.data?.results ||
      [];
    return list.map((ws) => ({ id: String(ws.id), name: ws.name }));
  }, [workspacesData]);

  // Invite form
  const inviteForm = useForm({
    defaultValues: {
      emails: [],
      orgLevel: isOwner || isAdmin ? undefined : LEVELS.VIEWER,
      wsLevel: LEVELS.WORKSPACE_MEMBER,
      workspaceIds: workspaceId ? [workspaceId] : [],
    },
    mode: "onChange",
    resolver: inviteFormSchema(existingEmails),
  });

  // Edit role form
  const editForm = useForm({
    defaultValues: {
      orgLevel: userData?.org_level ?? null,
      wsLevel: userData?.ws_level ?? null,
      workspaceIds: [],
      wsLevel_edit: LEVELS.WORKSPACE_MEMBER,
    },
    mode: "onChange",
    resolver: editRoleSchema,
  });

  // Resend invite form (allows role change before resending)
  const resendForm = useForm({
    defaultValues: {
      orgLevel: userData?.org_level ?? LEVELS.VIEWER,
    },
    mode: "onChange",
  });

  // Workspace role edit form
  const wsEditForm = useForm({
    defaultValues: {
      wsLevel: userData?.ws_level ?? null,
    },
    mode: "onChange",
  });

  // Watch edit form org level for workspace selector logic
  const watchedEditOrgLevel = editForm.watch("orgLevel");

  // Reset edit form when userData changes
  useEffect(() => {
    if (userData && openActionForm?.action === "edit-role") {
      editForm.reset({
        orgLevel: userData?.org_level ?? null,
        wsLevel: userData?.ws_level ?? null,
        workspaceIds: [],
        wsLevel_edit: LEVELS.WORKSPACE_MEMBER,
      });
    }
  }, [userData, openActionForm?.action, editForm]);

  // Reset ws edit form when userData changes
  useEffect(() => {
    if (userData && openActionForm?.action === "edit-ws-role") {
      wsEditForm.reset({
        wsLevel: userData?.ws_level ?? null,
      });
    }
  }, [userData, openActionForm?.action, wsEditForm]);

  // Reset resend form when userData changes
  useEffect(() => {
    if (userData && openActionForm?.action === "resend-invite") {
      resendForm.reset({
        orgLevel: userData?.org_level ?? LEVELS.VIEWER,
      });
    }
  }, [userData, openActionForm?.action, resendForm]);

  const refetchData = () => {
    queryClient.invalidateQueries({ queryKey: ["user-detail"] });
    gridApi?.refreshServerSide?.({ purge: true });
  };

  const handleOnClose = () => {
    inviteForm.reset();
    editForm.reset();
    wsEditForm.reset();
    resendForm.reset();
    onClose();
  };

  // ---------------------------------------------------------------
  // Mutations
  // ---------------------------------------------------------------

  // Invite (new RBAC endpoint)
  const { mutate: inviteMutate, isPending: isInvitePending } = useMutation({
    mutationFn: (data) => axios.post(endpoints.rbac.inviteCreate, data),
    onSuccess: (response, variables) => {
      const result = response?.data?.result;

      // Check if there are errors in the response
      if (result?.errors && result.errors.length > 0) {
        // Display all errors
        result.errors.forEach((error) => {
          enqueueSnackbar(error, { variant: "error" });
        });
        // Don't close form or refresh - let user try again
        return;
      }

      // Success case - show success message only if invites were sent
      const successCount = result?.invited?.length || 0;
      if (successCount > 0) {
        enqueueSnackbar(`Successfully invited ${successCount} user(s)`, {
          variant: "success",
        });
      }

      trackEvent(Events.workspaceSendInviteClicked, {
        [PropertyName.click]: "click",
        emails: variables?.emails,
        orgLevel: variables?.org_level,
      });
      refetchData();
      handleOnClose();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.result || "Failed to send invite",
        { variant: "error" },
      );
    },
  });

  // Edit role (new RBAC endpoint)
  const { mutate: updateRoleMutate, isPending: isRolePending } = useMutation({
    meta: { errorHandled: true },
    mutationFn: (data) => axios.post(endpoints.rbac.memberRoleUpdate, data),
    onSuccess: () => {
      enqueueSnackbar("User role updated", { variant: "info" });
      refetchData();
      handleOnClose();
    },
    onError: (error) => {
      enqueueSnackbar(error?.result || "Failed to update role", {
        variant: "error",
      });
    },
  });

  // Remove member (new RBAC endpoint)
  const { mutate: removeMemberMutate, isPending: isRemovePending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: (data) => axios.delete(endpoints.rbac.memberRemove, { data }),
      onSuccess: () => {
        enqueueSnackbar("Member removed from organization", {
          variant: "success",
        });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to remove member", {
          variant: "error",
        });
      },
    });

  // Update workspace role
  const { mutate: updateWsRoleMutate, isPending: isWsRolePending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: (data) =>
        axios.post(endpoints.rbac.workspaceMemberRoleUpdate(workspaceId), data),
      onSuccess: () => {
        enqueueSnackbar("Workspace role updated", { variant: "success" });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to update workspace role", {
          variant: "error",
        });
      },
    });

  // Remove from workspace
  const [showOrgRemoveConfirm, setShowOrgRemoveConfirm] = useState(false);
  const { mutate: removeWsMemberMutate, isPending: isWsRemovePending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: (data) =>
        axios.delete(endpoints.rbac.workspaceMemberRemove(workspaceId), {
          data,
        }),
      onSuccess: () => {
        enqueueSnackbar("Member removed from workspace", {
          variant: "success",
        });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        const msg = error?.result || "";
        if (msg.includes("only workspace")) {
          setShowOrgRemoveConfirm(true);
        } else {
          enqueueSnackbar(msg || "Failed to remove from workspace", {
            variant: "error",
          });
        }
      },
    });

  // Resend invite (new RBAC endpoint)
  const { mutate: resendInviteMutate, isPending: isResendPending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: (data) => axios.post(endpoints.rbac.inviteResend, data),
      onSuccess: () => {
        enqueueSnackbar("Invite resent successfully", { variant: "success" });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to resend invite", {
          variant: "error",
        });
      },
    });

  // Reactivate member (new RBAC endpoint)
  const { mutate: reactivateMutate, isPending: isReactivatePending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: (data) => axios.post(endpoints.rbac.memberReactivate, data),
      onSuccess: () => {
        enqueueSnackbar("Member reactivated successfully", {
          variant: "success",
        });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to reactivate member", {
          variant: "error",
        });
      },
    });

  // Cancel invite (new RBAC endpoint)
  const { mutate: cancelInviteMutate, isPending: isCancelPending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: (data) => axios.delete(endpoints.rbac.inviteCancel, { data }),
      onSuccess: () => {
        enqueueSnackbar("Invite cancelled", { variant: "success" });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to cancel invite", {
          variant: "error",
        });
      },
    });

  // Legacy mutations (kept for backward compat during transition)
  const { mutate: legacyResendInvite, isPending: isLegacyResendPending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: () =>
        axios.post(endpoints.workspace.resendInvite, { user_id: userData.id }),
      onSuccess: () => {
        enqueueSnackbar(`Invite resent to ${userData.name}`, {
          variant: "success",
        });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to resend invite", {
          variant: "error",
        });
      },
    });

  const { mutate: legacyRemoveInvite, isPending: isLegacyRemovePending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: () =>
        axios.post(endpoints.workspace.deleteUser, { user_id: userData.id }),
      onSuccess: () => {
        enqueueSnackbar(`Invite removed for ${userData.name}`, {
          variant: "success",
        });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to remove invite", {
          variant: "error",
        });
      },
    });

  const { mutate: _legacyDeactivate, isPending: isLegacyDeactivatePending } =
    useMutation({
      meta: { errorHandled: true },
      mutationFn: () =>
        axios.post(endpoints.workspace.deactivate, { user_id: userData.id }),
      onSuccess: () => {
        enqueueSnackbar(`${userData.name}'s account deactivated`, {
          variant: "success",
        });
        refetchData();
        handleOnClose();
      },
      onError: (error) => {
        enqueueSnackbar(error?.result || "Failed to deactivate user", {
          variant: "error",
        });
      },
    });

  // ---------------------------------------------------------------
  // Form handlers
  // ---------------------------------------------------------------

  const handleInvite = (formData) => {
    const orgLevel = formData.orgLevel ?? LEVELS.VIEWER;
    const wsLevel = formData.wsLevel ?? LEVELS.WORKSPACE_MEMBER;
    const workspaceIds = formData.workspaceIds || [];

    // Build workspace_access array
    const workspaceAccess =
      orgLevel >= LEVELS.ADMIN
        ? [] // Admin/Owner auto-access all workspaces
        : workspaceIds.map((wsId) => ({
            workspace_id: wsId,
            level: wsLevel,
          }));

    inviteMutate({
      emails: formData.emails,
      org_level: orgLevel,
      workspace_access: workspaceAccess,
    });
  };

  const handleEditRole = (formData) => {
    const needsWorkspaces =
      openActionForm?.action === "edit-role" &&
      formData.orgLevel != null &&
      formData.orgLevel < LEVELS.ADMIN;

    // Require workspace selection for Member/Viewer
    if (
      needsWorkspaces &&
      (!formData.workspaceIds || formData.workspaceIds.length === 0)
    ) {
      editForm.setError("workspaceIds", {
        message: "Please select at least one workspace",
      });
      return;
    }

    const payload = {
      user_id: userData.id,
    };
    if (formData.orgLevel != null) {
      payload.org_level = formData.orgLevel;
    }
    // Org-level edits with a workspace selection use `workspace_access` as
    // the authoritative desired set. Sending `ws_level + workspace_id` from
    // the page's URL context alongside it creates a contradictory payload —
    // the backend ends up keeping the URL-context workspace active even if
    // the user excluded it from their selection.
    if (needsWorkspaces && formData.workspaceIds?.length > 0) {
      const wsLevel = formData.wsLevel_edit ?? LEVELS.WORKSPACE_MEMBER;
      payload.workspace_access = formData.workspaceIds.map((wsId) => ({
        workspace_id: wsId,
        level: wsLevel,
      }));
    } else if (formData.wsLevel != null && workspaceId) {
      payload.ws_level = formData.wsLevel;
      payload.workspace_id = workspaceId;
    }
    updateRoleMutate(payload);
  };

  const handleRemoveMember = () => {
    if (userData?.type === "invite") {
      cancelInviteMutate({ invite_id: userData.id });
    } else {
      removeMemberMutate({ user_id: userData.id });
    }
  };

  const handleResendInvite = (formData) => {
    if (userData?.type === "invite") {
      const payload = { invite_id: userData.id };
      if (formData?.orgLevel != null) {
        payload.org_level = formData.orgLevel;
      }
      resendInviteMutate(payload);
    } else {
      legacyResendInvite();
    }
  };

  const handleCancelInvite = () => {
    if (userData?.type === "invite") {
      cancelInviteMutate({ invite_id: userData.id });
    } else {
      legacyRemoveInvite();
    }
  };

  const handleEditWsRole = (formData) => {
    updateWsRoleMutate({
      user_id: userData.id,
      ws_level: formData.wsLevel,
    });
  };

  const handleRemoveWsMember = () => {
    removeWsMemberMutate({ user_id: userData.id });
  };

  const handleReactivateMember = () => {
    reactivateMutate({ user_id: userData.id });
  };

  // Watch org level to auto-disable ws fields
  const watchedOrgLevel = inviteForm.watch("orgLevel");
  const isOrgAdminOrAbove =
    watchedOrgLevel != null && watchedOrgLevel >= LEVELS.ADMIN;

  // Show workspace selector for Member/Viewer in org-level edit
  const showEditWorkspaceSelector =
    openActionForm?.action === "edit-role" &&
    watchedEditOrgLevel != null &&
    watchedEditOrgLevel < LEVELS.ADMIN;

  return (
    <Box>
      {/* ============================================================ */}
      {/* INVITE USER */}
      {/* ============================================================ */}
      <ShowComponent
        condition={
          openActionForm?.action === "invite-user" ||
          openActionForm?.action === "invite-workspace-user"
        }
      >
        <ActionForm
          open={Boolean(openActionForm)}
          onClose={handleOnClose}
          showUserData={false}
          showRoleInfo
          title="Invite new users"
          formSection={
            <Box display="flex" flexDirection="column" gap={2}>
              {/* Emails */}
              <Box>
                <Controller
                  name="emails"
                  control={inviteForm.control}
                  rules={emailRules}
                  render={({ field, fieldState }) => (
                    <ChipsInput
                      {...field}
                      // @ts-ignore
                      error={fieldState.error?.message || ""}
                      chipContainerStyle={{ minHeight: "52px !important" }}
                      chipStyle={{
                        backgroundColor: `${alpha(theme.palette.primary.main, 0.1)} !important`,
                        color: `${theme.palette.primary.main} !important`,
                      }}
                      setError={inviteForm.setError}
                      label={"Emails"}
                      placeholder={"Emails, comma separated"}
                      validateItem={isValidEmail}
                      formatItem={formatItem}
                      getErrorMessage={() => "Please enter a valid email"}
                    />
                  )}
                />
              </Box>

              {/* Org Role dropdown — hidden for WS Admin */}
              {filteredOrgRoleOptions.length > 0 && (
                <FormSearchSelectFieldControl
                  control={inviteForm.control}
                  fieldName={"orgLevel"}
                  label="Organization Role"
                  required
                  options={filteredOrgRoleOptions}
                  showClear={false}
                  size="small"
                  fullWidth
                />
              )}

              {/* WS Role dropdown — disabled when org role is Admin+ */}
              {!isOrgAdminOrAbove && (
                <FormSearchSelectFieldControl
                  control={inviteForm.control}
                  fieldName={"wsLevel"}
                  label="Workspace Role"
                  options={wsRoleOptions}
                  showClear={false}
                  size="small"
                  fullWidth
                />
              )}

              {/* Workspace multi-select — hidden for Admin+ */}
              {!isOrgAdminOrAbove && (
                <Controller
                  name="workspaceIds"
                  control={inviteForm.control}
                  render={({ field, fieldState }) => (
                    <Autocomplete
                      multiple
                      size="small"
                      options={allWorkspaces}
                      getOptionLabel={(option) => option.name || ""}
                      value={allWorkspaces.filter((ws) =>
                        (field.value || []).includes(ws.id),
                      )}
                      onChange={(_, newValue) => {
                        field.onChange(newValue.map((ws) => ws.id));
                      }}
                      isOptionEqualToValue={(option, value) =>
                        option.id === value.id
                      }
                      renderTags={(value, getTagProps) =>
                        value.map((option, index) => (
                          <Chip
                            key={option.id}
                            label={option.name}
                            size="small"
                            sx={{
                              backgroundColor: theme.palette.primary.main,
                              ":hover": {
                                backgroundColor: theme.palette.primary.dark,
                              },
                            }}
                            {...getTagProps({ index })}
                          />
                        ))
                      }
                      renderInput={(params) => (
                        <TextField
                          {...params}
                          label="Workspaces"
                          placeholder="Select workspaces"
                          error={!!fieldState.error}
                          helperText={fieldState.error?.message}
                          required
                        />
                      )}
                    />
                  )}
                />
              )}
              {isOrgAdminOrAbove && (
                <Typography variant="body2" color="text.secondary">
                  Owner and Admin roles have automatic access to all workspaces.
                </Typography>
              )}
            </Box>
          }
          actionButton={
            <LoadingButton
              onClick={inviteForm.handleSubmit(handleInvite)}
              variant="contained"
              fullWidth
              color="primary"
              disabled={!inviteForm.formState.isValid}
              loading={isInvitePending}
            >
              Send Invite
            </LoadingButton>
          }
        />
      </ShowComponent>

      {/* ============================================================ */}
      {/* EDIT ROLE */}
      {/* ============================================================ */}
      <ShowComponent condition={openActionForm?.action === "edit-role"}>
        <ActionForm
          open={Boolean(openActionForm)}
          onClose={handleOnClose}
          userData={userData}
          title="Edit user info"
          showUserData
          showRoleInfo
          formSection={
            <Box display="flex" flexDirection="column" gap={2}>
              {/* Org Role */}
              <FormSearchSelectFieldControl
                control={editForm.control}
                fieldName={"orgLevel"}
                label="Organization Role"
                options={
                  filteredOrgRoleOptions.length > 0
                    ? filteredOrgRoleOptions
                    : orgRoleOptions
                }
                showClear={false}
                size="small"
                fullWidth
                disabled={!isOwner && !isAdmin}
              />
              {/* WS Role — only shown in workspace-scoped view, hidden when workspace selector is active */}
              {workspaceId && !showEditWorkspaceSelector && (
                <FormSearchSelectFieldControl
                  control={editForm.control}
                  fieldName={"wsLevel"}
                  label="Workspace Role"
                  options={wsRoleOptions}
                  showClear={false}
                  size="small"
                  fullWidth
                  disabled={
                    userData?.org_level != null &&
                    userData.org_level >= LEVELS.ADMIN
                  }
                />
              )}

              {/* Workspace access selector — shown when demoting Admin+ to Member/Viewer */}
              {showEditWorkspaceSelector && (
                <>
                  <FormSearchSelectFieldControl
                    control={editForm.control}
                    fieldName={"wsLevel_edit"}
                    label="Workspace Role"
                    options={wsRoleOptions}
                    showClear={false}
                    size="small"
                    fullWidth
                  />
                  <Controller
                    name="workspaceIds"
                    control={editForm.control}
                    render={({ field, fieldState }) => (
                      <Autocomplete
                        multiple
                        size="small"
                        options={allWorkspaces}
                        getOptionLabel={(option) => option.name || ""}
                        value={allWorkspaces.filter((ws) =>
                          (field.value || []).includes(ws.id),
                        )}
                        onChange={(_, newValue) => {
                          field.onChange(newValue.map((ws) => ws.id));
                        }}
                        isOptionEqualToValue={(option, value) =>
                          option.id === value.id
                        }
                        renderTags={(value, getTagProps) =>
                          value.map((option, index) => (
                            <Chip
                              key={option.id}
                              label={option.name}
                              size="small"
                              sx={{ backgroundColor: "primary.main" }}
                              {...getTagProps({ index })}
                            />
                          ))
                        }
                        renderInput={(params) => (
                          <TextField
                            {...params}
                            label="Workspaces"
                            placeholder="Select workspaces to grant access"
                            error={!!fieldState.error}
                            helperText={fieldState.error?.message}
                            required
                          />
                        )}
                      />
                    )}
                  />
                  <Typography variant="body2" color="text.secondary">
                    Members and Viewers need explicit workspace access. Select
                    which workspaces this user should have access to.
                  </Typography>
                </>
              )}
            </Box>
          }
          actionButton={
            <LoadingButton
              onClick={editForm.handleSubmit(handleEditRole)}
              variant="contained"
              fullWidth
              color="primary"
              loading={isRolePending}
            >
              Update
            </LoadingButton>
          }
        />
      </ShowComponent>

      {/* ============================================================ */}
      {/* REMOVE MEMBER (replaces deactivate) */}
      {/* ============================================================ */}
      <ShowComponent
        condition={
          openActionForm?.action === "remove-member" ||
          openActionForm?.action === "deactivate"
        }
      >
        <ActionForm
          open={Boolean(openActionForm)}
          onClose={handleOnClose}
          userData={userData}
          showUserData
          showRole
          title="Are you sure you want to remove this member?"
          actionButton={
            <Box display="flex" gap={2}>
              <Button variant="outlined" onClick={handleOnClose}>
                Cancel
              </Button>
              <LoadingButton
                onClick={handleRemoveMember}
                variant="contained"
                color="error"
                loading={
                  isRemovePending ||
                  isCancelPending ||
                  isLegacyDeactivatePending
                }
              >
                Remove
              </LoadingButton>
            </Box>
          }
        />
      </ShowComponent>

      {/* ============================================================ */}
      {/* RESEND INVITE */}
      {/* ============================================================ */}
      <ShowComponent condition={openActionForm?.action === "resend-invite"}>
        <ActionForm
          open={Boolean(openActionForm)}
          onClose={handleOnClose}
          userData={userData}
          showUserData
          showRoleInfo
          title="Select role and resend invite"
          formSection={
            <Box display="flex" flexDirection="column" gap={2}>
              <FormSearchSelectFieldControl
                control={resendForm.control}
                fieldName={"orgLevel"}
                label="Organization Role"
                options={
                  filteredOrgRoleOptions.length > 0
                    ? filteredOrgRoleOptions
                    : orgRoleOptions
                }
                showClear={false}
                size="small"
                fullWidth
              />
            </Box>
          }
          actionButton={
            <Box display="flex" gap={2}>
              <Button variant="outlined" onClick={handleOnClose}>
                Cancel
              </Button>
              <LoadingButton
                onClick={resendForm.handleSubmit(handleResendInvite)}
                variant="contained"
                color="primary"
                loading={isResendPending || isLegacyResendPending}
              >
                Send
              </LoadingButton>
            </Box>
          }
        />
      </ShowComponent>

      {/* ============================================================ */}
      {/* CANCEL INVITE / REMOVE INVITE */}
      {/* ============================================================ */}
      <ShowComponent
        condition={
          openActionForm?.action === "cancel-invite" ||
          openActionForm?.action === "remove-invite"
        }
      >
        <ActionForm
          open={Boolean(openActionForm)}
          onClose={handleOnClose}
          userData={userData}
          showUserData
          showRole
          title="Are you sure you want to cancel this invite?"
          actionButton={
            <Box display="flex" gap={2}>
              <Button variant="outlined" onClick={handleOnClose}>
                Cancel
              </Button>
              <LoadingButton
                onClick={handleCancelInvite}
                variant="contained"
                color="error"
                loading={isCancelPending || isLegacyRemovePending}
              >
                Remove
              </LoadingButton>
            </Box>
          }
        />
      </ShowComponent>

      {/* ============================================================ */}
      {/* EDIT WORKSPACE ROLE */}
      {/* ============================================================ */}
      <ShowComponent condition={openActionForm?.action === "edit-ws-role"}>
        <ActionForm
          open={Boolean(openActionForm)}
          onClose={handleOnClose}
          userData={userData}
          title="Edit workspace role"
          showUserData
          showRoleInfo
          formSection={
            <Box display="flex" flexDirection="column" gap={2}>
              <FormSearchSelectFieldControl
                control={wsEditForm.control}
                fieldName={"wsLevel"}
                label="Workspace Role"
                options={wsRoleOptions}
                showClear={false}
                size="small"
                fullWidth
                disabled={
                  userData?.org_level != null &&
                  userData.org_level >= LEVELS.ADMIN
                }
              />
              {userData?.org_level != null &&
                userData.org_level >= LEVELS.ADMIN && (
                  <Typography variant="body2" color="text.secondary">
                    This user is an org Admin or above and automatically has
                    Workspace Admin access.
                  </Typography>
                )}
            </Box>
          }
          actionButton={
            <LoadingButton
              onClick={wsEditForm.handleSubmit(handleEditWsRole)}
              variant="contained"
              fullWidth
              color="primary"
              loading={isWsRolePending}
              disabled={
                userData?.org_level != null &&
                userData.org_level >= LEVELS.ADMIN
              }
            >
              Update
            </LoadingButton>
          }
        />
      </ShowComponent>

      {/* ============================================================ */}
      {/* REMOVE FROM WORKSPACE */}
      {/* ============================================================ */}
      <ShowComponent condition={openActionForm?.action === "remove-ws-member"}>
        {showOrgRemoveConfirm ? (
          <ActionForm
            open={Boolean(openActionForm)}
            onClose={() => {
              setShowOrgRemoveConfirm(false);
              handleOnClose();
            }}
            userData={userData}
            showUserData
            showRole
            title="This is the user's only workspace. Removing them will also remove them from the organization entirely."
            actionButton={
              <Box display="flex" gap={2}>
                <Button
                  variant="outlined"
                  onClick={() => {
                    setShowOrgRemoveConfirm(false);
                    handleOnClose();
                  }}
                >
                  Cancel
                </Button>
                <LoadingButton
                  onClick={() => {
                    removeMemberMutate({ user_id: userData.id });
                    setShowOrgRemoveConfirm(false);
                  }}
                  variant="contained"
                  color="error"
                  loading={isRemovePending}
                >
                  Remove from organization
                </LoadingButton>
              </Box>
            }
          />
        ) : (
          <ActionForm
            open={Boolean(openActionForm)}
            onClose={handleOnClose}
            userData={userData}
            showUserData
            showRole
            title="Are you sure you want to remove this member from the workspace?"
            actionButton={
              <Box display="flex" gap={2}>
                <Button variant="outlined" onClick={handleOnClose}>
                  Cancel
                </Button>
                <LoadingButton
                  onClick={handleRemoveWsMember}
                  variant="contained"
                  color="error"
                  loading={isWsRemovePending}
                >
                  Remove
                </LoadingButton>
              </Box>
            }
          />
        )}
      </ShowComponent>

      {/* ============================================================ */}
      {/* REACTIVATE MEMBER */}
      {/* ============================================================ */}
      <ShowComponent condition={openActionForm?.action === "reactivate-member"}>
        <ActionForm
          open={Boolean(openActionForm)}
          onClose={handleOnClose}
          userData={userData}
          showUserData
          showRole
          title="Reactivate this member?"
          formSection={
            <Typography variant="body2" color="text.secondary">
              {userData?.email} will regain access to their previously assigned
              workspaces.
            </Typography>
          }
          actionButton={
            <Box display="flex" gap={2}>
              <Button variant="outlined" onClick={handleOnClose}>
                Cancel
              </Button>
              <LoadingButton
                onClick={handleReactivateMember}
                variant="contained"
                color="primary"
                loading={isReactivatePending}
              >
                Reactivate
              </LoadingButton>
            </Box>
          }
        />
      </ShowComponent>
    </Box>
  );
};

export default AllActionForm;

AllActionForm.propTypes = {
  openActionForm: PropTypes.object,
  userData: PropTypes.object,
  onClose: PropTypes.func,
  gridApi: PropTypes.object,
  workspaceId: PropTypes.string,
};
