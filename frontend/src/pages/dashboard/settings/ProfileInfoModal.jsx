import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import React, { useEffect } from "react";
import Iconify from "src/components/iconify/iconify";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { FullNameFormValidation } from "./validation";
import { LoadingButton } from "@mui/lab";
import Drawer from "@mui/material/Drawer";
import { enqueueSnackbar } from "notistack";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import logger from "src/utils/logger";
import { useMutation } from "@tanstack/react-query";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const ProfileInfoModal = ({ open, onClose, fullName, setRefreshData }) => {
  const theme = useTheme();

  const { handleSubmit, control, reset } = useForm({
    resolver: zodResolver(FullNameFormValidation),
    defaultValues: {},
  });

  const { mutate: editFullName, isPending: isEditFullNameLoading } =
    useMutation({
      mutationFn: async (formValues) => {
        const data = {
          name: formValues.name,
        };
        const response = await axios.post(
          endpoints.stripe.updateUserFullName,
          data,
        );
        return response;
      },
      onSuccess: (response) => {
        if (response.status === 200) {
          enqueueSnackbar("Full name has been updated.", {
            variant: "success",
          });
          setRefreshData((prev) => !prev);
        } else {
          enqueueSnackbar("Failed to update full name.", {
            variant: "error",
          });
        }
        onClose();
      },
      onError: (error) => {
        logger.error("Error updating full name:", error);
        enqueueSnackbar("Failed to update full name.", {
          variant: "error",
        });
      },
    });

  useEffect(() => {
    if (open) {
      reset({
        name: fullName ? fullName : "",
      });
    }
  }, [open, fullName, reset]);

  const handleEditFullName = (formValues) => {
    trackEvent(Events.updateFullNameClicked, {
      [PropertyName.formFields]: {
        newName: formValues.name,
      },
    });
    editFullName(formValues);
  };

  const covered = useDrawerCovered(open);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "480px",
        },
      }}
    >
      <form
        onSubmit={handleSubmit(handleEditFullName)}
        style={{
          width: "100%",
          flex: 1,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box
          sx={{
            display: open ? "flex" : "none",
            flex: 1,
            width: "100%",
            padding: theme.spacing(2),
            flexDirection: "column",
            gap: theme.spacing(2),
          }}
        >
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Typography
              variant="m3"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Update Name
            </Typography>
            <Iconify
              icon="solar:close-linear"
              onClick={onClose}
              color="text.primary"
              sx={{
                cursor: "pointer",
              }}
            />
          </Box>

          <Stack direction="column" spacing={2}>
            <FormTextFieldV2
              label="Full Name"
              placeholder="Enter your full name"
              size="small"
              control={control}
              fieldName="name"
              autoFocus
            />
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                gap: theme.spacing(1),
                mt: "auto",
                mx: -2,
                mb: -2,
                px: 2,
                py: 1.5,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Button
                type="button"
                variant="outlined"
                fullWidth
                onClick={onClose}
              >
                <Typography
                  variant="s2"
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  Cancel
                </Typography>
              </Button>
              <LoadingButton
                type="submit"
                loading={isEditFullNameLoading}
                fullWidth
                variant="contained"
                color="primary"
              >
                Update Full Name
              </LoadingButton>
            </Box>
          </Stack>
        </Box>
      </form>
    </Drawer>
  );
};

ProfileInfoModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  userData: PropTypes.object,
  type: PropTypes.string,
  fullName: PropTypes.string,
  setRefreshData: PropTypes.func,
};

export default ProfileInfoModal;
