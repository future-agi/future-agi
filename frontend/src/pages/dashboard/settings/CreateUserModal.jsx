import { Box, Button, Stack, Typography } from "@mui/material";
import React, { useState, useEffect } from "react";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { CreateUserFormValidation } from "./validation";
import { LoadingButton } from "@mui/lab";
import Drawer from "@mui/material/Drawer"; // Import Drawer
import { enqueueSnackbar } from "notistack";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { RESPONSE_CODES } from "src/utils/constants";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import logger from "src/utils/logger";

const CreateUserModal = ({ open, onClose, userData, type, setRefreshData }) => {
  const [isLoading, setIsLoading] = useState(false);
  if (type !== "create" && type !== "editUser" && type !== "editInvitation") {
    type = "create";
  }

  const { handleSubmit, control, reset } = useForm({
    resolver: zodResolver(CreateUserFormValidation),
    defaultValues: {
      userName: userData ? userData.name : "",
      email: userData ? userData.email : "",
      organization_role: userData ? userData.organization_role ?? "" : "",
    },
  });

  // const watchUserName = watch("userName");
  // const watchEmail = watch("email");
  // const watchOrganizationRole = watch("organization_role");

  // const debouncedUserName = useDebounce(watchUserName, 500);
  // const debouncedEmail = useDebounce(watchEmail, 500);
  // const debouncedOrganizationRole = useDebounce(watchOrganizationRole, 500);

  // useEffect(() => {
  //   if (debouncedUserName) {
  //     trackEvent(Events.newUserNameEntered);
  //   }
  // }, [debouncedUserName]);

  // useEffect(() => {
  //   if (debouncedEmail) {
  //     trackEvent(Events.newUserEmailEntered);
  //   }
  // }, [debouncedEmail]);

  // useEffect(() => {
  //   if (debouncedOrganizationRole) {
  //     trackEvent(Events.Accesessselected);
  //   }
  // }, [debouncedOrganizationRole]);

  const handleResend = async (invitationId) => {
    trackEvent(Events.resendInviteClicked, {
      [PropertyName.email]: userData.email,
    });

    try {
      const response = await axios.post(
        endpoints.stripe.resendInvitationEmails,
        {
          user_ids: [invitationId],
        },
      );

      if (response.status === 200) {
        enqueueSnackbar("Invitation resent successfully", {
          variant: "success",
        });
      } else {
        enqueueSnackbar("Failed to resend invitation", { variant: "error" });
      }
    } catch (error) {
      logger.error("Error resending invitation:", error);
    }
  };

  const handleDelete = async (invitationId, setRefreshData) => {
    trackEvent(Events.deleteInviteClicked, {
      [PropertyName.email]: userData.email,
    });
    try {
      const response = await axios.delete(endpoints.stripe.deleteUsers, {
        data: {
          user_ids: [invitationId],
        },
      });
      if (response.status === 200) {
        enqueueSnackbar("User deleted successfully", { variant: "success" });
      } else {
        enqueueSnackbar("Failed to delete user", { variant: "error" });
      }
    } catch (error) {
      logger.error("Error deleting invitation:", error);
      if (error?.error) {
        enqueueSnackbar("Failed to delete user : " + error?.error, {
          variant: "error",
        });
      } else {
        enqueueSnackbar("Failed to delete user", { variant: "error" });
      }
    } finally {
      setRefreshData((prev) => !prev);
    }
  };

  useEffect(() => {
    if (open) {
      reset({
        userName: userData ? userData.name : "",
        email: userData ? userData.email : "",
        organization_role: userData ? userData.organization_role ?? "" : "",
      });
    }
  }, [open, userData, reset]);

  const handleUpdateUser = async (formValues) => {
    setIsLoading(true);
    const data = {
      email: formValues.email,
      name: formValues.userName,
      organization_role: formValues.organization_role,
      user_id: userData.id,
    };

    try {
      const response = await axios.post(endpoints.stripe.updateUser, data, {
        validateStatus: (status) =>
          status >= RESPONSE_CODES.SUCCESS &&
          status < RESPONSE_CODES.INTERNAL_SERVER,
      });
      const { status, result } = response.data;
      const message =
        result ||
        (status ? "Updated user successfully" : "Failed to update user");

      enqueueSnackbar(message, { variant: status ? "success" : "error" });
      if (status) setRefreshData((prev) => !prev);
    } catch (error) {
      const errorMessage =
        error.response?.data?.result || "Failed to update user";
      enqueueSnackbar(errorMessage, { variant: "error" });
    } finally {
      setIsLoading(false);
      onClose();
    }
  };

  const handleCreateUser = async (formValues) => {
    setIsLoading(true);

    const data = {
      members: [
        {
          email: formValues.email,
          name: formValues.userName,
          organization_role: formValues.organization_role,
        },
      ],
    };
    trackEvent(Events.createUserClicked, {
      name: data.members[0].name,
      email: data.members[0].email,
      user_role: data.members[0].organization_role,
    });

    try {
      const response = await axios.post(
        endpoints.settings.teams.inviteMember,
        data,
        {
          validateStatus: (status) => status < 500, // This prevents Axios from throwing errors for 4xx responses
        },
      );
      if (response.status == 200 || response.status == 201) {
        if (response.data?.status) {
          enqueueSnackbar("Invite sent successfully.", { variant: "success" });
          onClose(); // Close the modal
          setRefreshData((prev) => !prev);
        } else {
          const errorMessage =
            response.data?.result?.errors[0]?.error || "Something went wrong.";
          enqueueSnackbar(errorMessage, { variant: "error" });
        }
      } else {
        if (response?.status == RESPONSE_CODES.LIMIT_REACHED) return;
        const errorMessage =
          response.data?.result?.errors[0]?.error || "Something went wrong.";
        enqueueSnackbar(errorMessage, { variant: "error" });
      }
    } catch (error) {
      const errorMessage = "Something went wrong.";
      enqueueSnackbar(errorMessage, { variant: "error" });
    } finally {
      setIsLoading(false); // Ensure loading state is reset
    }
  };

  const OrganizationRoleOptions = [
    // { label: "Admin", value: "Admin" },
    { label: "Member", value: "Member" },
    { label: "Owner", value: "Owner" },
  ];

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "400px",
          position: "fixed",
          zIndex: 1000,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
    >
      <form onSubmit={handleSubmit(handleCreateUser)} style={{ width: "100%" }}>
        <Box
          sx={{
            display: open ? "flex" : "none",
            position: "fixed",
            right: 0,
            top: 0,
            height: "100%",
            width: "400px",
            backgroundColor: "background.paper",
            padding: 2,
            boxShadow: 3,
            zIndex: 1000,
            flexDirection: "column",
            gap: 2,
          }}
        >
          <Typography
            variant="subtitle1"
            color="text.primary"
            sx={{
              marginTop: 1,
              marginBottom: 2,
            }}
          >
            {type === "create" && "Add User"}
            {type === "editUser" && "Update User Details"}
            {type === "editInvitation" && "Edit Invitation"}
          </Typography>

          <FormTextFieldV2
            label="Name"
            control={control}
            fieldName="userName"
            placeholder="John Doe"
            autoFocus
            disabled={type === "editInvitation"}
          />
          <FormTextFieldV2
            label="Email"
            control={control}
            fieldName="email"
            placeholder="example@example.com"
            disabled={type !== "create"}
          />
          <FormSearchSelectFieldControl
            label="Access"
            showClear={false}
            control={control}
            fieldName="organization_role"
            options={OrganizationRoleOptions}
            placeholder="Admin Access"
            disabled={type === "editInvitation"}
            MenuProps={{
              PaperProps: {
                sx: {
                  maxHeight: 224,
                },
              },
            }}
          />

          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              position: "absolute",
              bottom: 16,
              width: "100%",
            }}
          >
            {type === "create" && (
              <Stack
                direction="row"
                spacing={2}
                sx={{ width: "100%" }}
                marginRight={5}
                marginBottom={1}
              >
                <Button
                  variant="outlined"
                  onClick={() => {
                    trackEvent(Events.createUserCancelled);
                    onClose();
                  }}
                  sx={{ flex: 1 }}
                >
                  Cancel
                </Button>
                <LoadingButton
                  loading={isLoading}
                  variant="contained"
                  color="primary"
                  type="submit"
                  sx={{ flex: 1 }}
                >
                  Create User
                </LoadingButton>
              </Stack>
            )}
            {type === "editUser" && (
              <Stack
                direction="row"
                spacing={2}
                sx={{ width: "100%" }}
                marginRight={5}
                marginBottom={1}
              >
                <Button variant="outlined" onClick={onClose} sx={{ flex: 1 }}>
                  Cancel
                </Button>
                <LoadingButton
                  loading={isLoading}
                  variant="contained"
                  color="primary"
                  sx={{ flex: 1 }}
                  onClick={handleSubmit(handleUpdateUser)}
                >
                  Update User
                </LoadingButton>
              </Stack>
            )}
            {type === "editInvitation" && (
              <Stack
                direction="row"
                spacing={2}
                sx={{ width: "100%" }}
                marginRight={5}
                marginBottom={1}
              >
                {/* <Button variant="outlined" onClick={onClose} sx={{ flex: 1 }}>Cancel</Button> */}
                <LoadingButton
                  loading={isLoading}
                  variant="contained"
                  color="primary"
                  sx={{ flex: 1 }}
                  onClick={() => handleDelete(userData.id, setRefreshData)}
                >
                  Delete User
                </LoadingButton>
                <LoadingButton
                  loading={isLoading}
                  variant="contained"
                  color="primary"
                  sx={{ flex: 1 }}
                  onClick={() => handleResend(userData.id)}
                >
                  Resend Invitation
                </LoadingButton>
              </Stack>
            )}
          </Box>
        </Box>
      </form>
    </Drawer>
  );
};

CreateUserModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  userData: PropTypes.object,
  type: PropTypes.string,
  setRefreshData: PropTypes.func,
};

export default CreateUserModal;
