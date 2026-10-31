import React, { useEffect, useState } from "react";
import PropType from "prop-types";
import {
  Box,
  Button,
  FormControl,
  Typography,
  InputAdornment,
  Divider,
  Switch,
  Modal,
  useTheme,
} from "@mui/material";
import { useForm } from "react-hook-form";
import { EditAutoReloadSettingsFormValidation } from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useSnackbar } from "src/components/snackbar";
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import { CreditCardIcon } from "./IconComponents";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const EditAutoReloadSettingsModal = ({
  open,
  onClose,
  handleAutoRechargeClick,
  last4digits,
  walletTopupAmount,
  topupThreshold,
  autoReload,
  setRefreshBillingPage,
}) => {
  const { enqueueSnackbar } = useSnackbar();
  const theme = useTheme();

  const { handleSubmit, control, reset, watch } = useForm({
    resolver: zodResolver(EditAutoReloadSettingsFormValidation),
    defaultValues: {
      amount: 0,
      threshold: 0,
    },
  });

  const watchAmount = watch("amount");
  const watchThreshold = watch("threshold");

  // Check if form is valid for enabling save button and showing summary
  const isFormValid = Boolean(watchAmount && watchThreshold && last4digits);

  const handleSaveAutoReloadSettings = async (formValues) => {
    {
      const response = await axios.post(
        endpoints.stripe.updateAutoReloadSettings,
        {
          autoreload_enabled: autoReloadState,
          autoreload_walletamount: formValues.amount,
          autoreload_walletthreshold: formValues.threshold,
        },
      );
      if (response.status === 200) {
        onClose();
        enqueueSnackbar("Auto reload settings updated successfully", {
          variant: "success",
        });
        if (autoReloadState) {
          enqueueSnackbar("Your auto reload is now enabled", {
            variant: "success",
          });
        } else {
          enqueueSnackbar("Your auto reload is now disabled", {
            variant: "warning",
          });
        }
        setRefreshBillingPage((prev) => !prev);
      } else {
        enqueueSnackbar("Failed to update auto reload settings", {
          variant: "error",
        });
      }
    }
  };

  const [autoReloadState, setAutoReloadState] = useState(autoReload);
  const handleAutoReloadChange = (event) => {
    setAutoReloadState(event.target.checked);
  };

  const onSubmit = (formValues) => {
    handleSaveAutoReloadSettings(formValues);
    trackEvent(Events.saveSettingsClicked, {
      [PropertyName.formFields]: { formValues },
    });
  };

  useEffect(() => {
    reset({
      amount: walletTopupAmount,
      threshold: topupThreshold,
    });
    setAutoReloadState(autoReload);
  }, [autoReload, walletTopupAmount, topupThreshold]);

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
    >
      <Box
        sx={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 560,
          bgcolor: "background.paper",
          // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
          boxShadow: 24,
          borderRadius: theme.spacing(1),
        }}
      >
        <form onSubmit={handleSubmit(onSubmit)}>
          <Box
            sx={{
              padding: theme.spacing(2),
              display: "flex",
              flexDirection: "column",
              gap: theme.spacing(2.5),
              width: "100%",
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
                color={"text.primary"}
                fontWeight={"fontWeightSemiBold"}
              >
                Auto Reload Settings
              </Typography>
              <Iconify
                icon="solar:close-linear"
                onClick={() => onClose()}
                sx={{ cursor: "pointer" }}
              />
            </Box>

            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: theme.spacing(1),
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: theme.spacing(0.5),
                }}
              >
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  Enable Auto-Reload
                </Typography>
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                  color={"text.primary"}
                >
                  Auto-reload credit when balance reaches a certain threshold.
                  Enabling reloads is recommended to avoid service disruptions.
                </Typography>
              </Box>
              <Switch
                checked={autoReloadState}
                onChange={handleAutoReloadChange}
                color="success"
                onClick={() => {
                  trackEvent(Events.autoReloadClicked);
                }}
              />
            </Box>

            {autoReloadState && (
              <>
                <Divider sx={{ borderColor: "divider" }} />
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightMedium"}
                  sx={{ color: "text.primary" }}
                >
                  Reload Threshold
                </Typography>
                <FormControl
                  fullWidth
                  sx={{ flex: 1, gap: theme.spacing(2.5) }}
                >
                  <FormTextFieldV2
                    label="Threshold Value"
                    size="small"
                    control={control}
                    fieldName="threshold"
                    placeholder="Enter threshold value"
                    autoFocus
                    required
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">$</InputAdornment>
                      ),
                      inputProps: {
                        type: "number",
                        step: "0.01",
                        min: "0",
                        title: "Please enter a valid number",
                      },
                    }}
                    onChange={() => {
                      trackEvent(Events.reloadThresholdAmountAdded);
                    }}
                  />
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: theme.spacing(0.5),
                    }}
                  >
                    <FormTextFieldV2
                      label="Amount to add"
                      size="small"
                      control={control}
                      fieldName="amount"
                      placeholder="Enter amount to be added"
                      required
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">$</InputAdornment>
                        ),
                        inputProps: {
                          type: "number",
                          step: "0.01",
                          min: "0",
                          title: "Please enter a valid number",
                        },
                      }}
                      onChange={() => {
                        trackEvent(Events.reloadAmountEntered);
                      }}
                    />
                    <Typography
                      variant="s3"
                      fontWeight={"fontWeightRegular"}
                      sx={{ color: "text.disabled" }}
                    >
                      This amount will be added to your balance when it falls
                      below the threshold
                    </Typography>
                  </Box>
                  {watchThreshold && watchAmount && (
                    <Box
                      sx={{
                        p: theme.spacing(1.5),
                        backgroundColor: "background.neutral",
                        borderRadius: theme.spacing(0.5),
                        display: "flex",
                        flexDirection: "column",
                        gap: theme.spacing(0.5),
                      }}
                    >
                      <Typography
                        variant="s1"
                        fontWeight={"fontWeightSemiBold"}
                        color="text.primary"
                      >
                        Summary
                      </Typography>
                      <Typography
                        variant="s1"
                        fontWeight={"fontWeightRegular"}
                        color="text.primary"
                      >
                        When your balance falls below ${watchThreshold}, we’ll
                        automatically add ${watchAmount} to your account
                      </Typography>
                    </Box>
                  )}
                </FormControl>
                <Divider />
                <Box>
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    sx={{ color: "text.primary" }}
                  >
                    Billed To
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      marginTop: theme.spacing(1),
                      gap: theme.spacing(1),
                    }}
                  >
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        width: "70%",
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: theme.spacing(0.5),
                        padding: theme.spacing(1),
                        height: "38px",
                      }}
                    >
                      <CreditCardIcon
                        width={25}
                        height={25}
                        fill="none"
                        stroke="grey"
                        strokeWidth={1.0}
                      />
                      <Typography
                        variant="s1"
                        fontWeight="fontWeightMedium"
                        color="text.disabled"
                        sx={{
                          marginRight: theme.spacing(1.25),
                          marginLeft: theme.spacing(1),
                        }}
                      >
                        {last4digits ? `************${last4digits}` : "N/A"}
                      </Typography>
                    </Box>
                    <Button
                      variant="outlined"
                      color="primary"
                      sx={{
                        width: "30%",
                      }}
                      onClick={() => {
                        handleAutoRechargeClick();
                      }}
                    >
                      Update Card
                    </Button>
                  </Box>
                  {!last4digits && (
                    <Typography
                      variant="s3"
                      fontWeight={"fontWeightRegular"}
                      sx={{ color: "text.disabled" }}
                    >
                      Please add a card to enable auto reload
                    </Typography>
                  )}
                </Box>

                <Divider sx={{ borderColor: "divider" }} />
              </>
            )}
            <Box
              sx={{
                display: "flex",
                justifyContent: "end",
                gap: theme.spacing(1.5),
              }}
            >
              <Button
                variant="outlined"
                sx={{ width: "50%" }}
                onClick={() => {
                  onClose();
                }}
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
                disabled={!isFormValid}
                variant="contained"
                color="primary"
                type="submit"
                sx={{
                  width: "50%",
                  "&:disabled": {
                    color: "common.white",
                    backgroundColor: "action.hover",
                  },
                }}
              >
                <Typography
                  variant="s2"
                  fontWeight={"fontWeightMedium"}
                  color={"white"}
                >
                  Save Settings
                </Typography>
              </LoadingButton>
            </Box>
          </Box>
        </form>
        {/* </div> */}
      </Box>
    </Modal>
  );
};

EditAutoReloadSettingsModal.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
  handleAutoRechargeClick: PropType.func,
  last4digits: PropType.string,
  walletTopupAmount: PropType.number,
  topupThreshold: PropType.number,
  setWalletTopupAmount: PropType.func,
  setTopupThreshold: PropType.func,
  autoReload: PropType.bool,
  setRefreshBillingPage: PropType.func,
};

export default EditAutoReloadSettingsModal;
