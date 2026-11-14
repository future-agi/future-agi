import React, { useState } from "react";
import PropType from "prop-types";
import {
  Box,
  Button,
  FormControl,
  Typography,
  InputAdornment,
  Divider,
  Modal,
  useTheme,
} from "@mui/material";
import { useForm } from "react-hook-form";
import { AddFundsFormValidation } from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useSnackbar } from "src/components/snackbar";
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";
import { stripePromise } from "./stripeVariables";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import { CreditCardIcon } from "./IconComponents";
import logger from "src/utils/logger";

const AddFundsModal = ({
  open,
  onClose,
  last4digits,
  handleAutoRechargeClick,
}) => {
  const { enqueueSnackbar } = useSnackbar();
  const theme = useTheme();
  const [isLoading, setIsLoading] = useState(false);

  const { handleSubmit, control, reset, watch } = useForm({
    resolver: zodResolver(AddFundsFormValidation),
    defaultValues: {
      amount: "",
    },
  });

  const watchAmount = watch("amount");
  const amount = parseFloat(watchAmount) || 0;
  // const estimatedTaxes = amount * 0.18; // 18% tax
  const estimatedTaxes = amount * 0; // Changing this to 0 taxes on chintan request

  const total = amount + estimatedTaxes;

  const closeAndClear = () => {
    reset();
    onClose();
  };

  const handleAmountChange = (value) => {
    const numValue = parseFloat(value) || 0;
    if (numValue > 0) {
      trackEvent(Events.fundAmountEntered);
    }
  };

  const handleCustomPayment = async (formValues) => {
    setIsLoading(true);
    try {
      const stripe = await stripePromise;
      if (!stripe) {
        enqueueSnackbar("Failed to create checkout session", {
          variant: "error",
        });
        setIsLoading(false);
        return;
      }
      const response = await axios.post(
        endpoints.stripe.createCustomPaymentCheckoutSession,
        formValues,
      );

      const { error } = await stripe.redirectToCheckout({
        sessionId: response.data.sessionId,
      });

      if (error) {
        logger.error("Error during checkout:", error);
        enqueueSnackbar("Payment failed. Please try again.", {
          variant: "error",
        });
      } else {
        // Success case - this will be handled by the redirect callback
        enqueueSnackbar("Your credit balance has been updated", {
          variant: "success",
        });
      }
    } catch (err) {
      logger.error("Failed to create checkout session:", err);
      enqueueSnackbar("Failed to process payment. Please try again.", {
        variant: "error",
      });
    } finally {
      setIsLoading(false);
    }
  };

  const onSubmit = (formValues) => {
    trackEvent(Events.purchaseClicked);
    trackEvent(Events.purchaseClicked, {
      [PropertyName.count]: {
        formValues,
      },
    });
    handleCustomPayment(formValues);
  };

  return (
    <Modal open={open} onClose={closeAndClear}>
      <Box
        sx={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 560,
          bgcolor: "background.paper",
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
            {/* Header */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Typography
                variant="m3"
                color="text.primary"
                fontWeight="fontWeightSemiBold"
              >
                Add Funds
              </Typography>
              <Iconify
                icon="solar:close-linear"
                onClick={closeAndClear}
                sx={{ cursor: "pointer", color: "text.primary" }}
              />
            </Box>

            {/* Credits Input */}
            <FormControl fullWidth>
              <FormTextFieldV2
                label="Credits To Purchase"
                control={control}
                size="small"
                fieldName="amount"
                autoFocus
                placeholder="Enter amount"
                required
                helperText=""
                defaultValue=""
                onBlur={() => {}}
                onChange={handleAmountChange}
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
              />
            </FormControl>

            <Divider sx={{ borderColor: "divider" }} />
            {/* Fund Details - Always show */}
            <Box>
              <Typography
                variant="s1"
                fontWeight="fontWeightSemiBold"
                color="text.primary"
                sx={{ display: "block", mb: theme.spacing(1) }}
              >
                Fund Details
              </Typography>

              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: theme.spacing(1),
                }}
              >
                <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                  <Typography
                    variant="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    Sub total
                  </Typography>
                  <Typography
                    variant="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    {amount > 0 ? `US$${amount.toFixed(2)}` : "-"}
                  </Typography>
                </Box>

                <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                  <Typography
                    variant="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    Estimated Taxes
                  </Typography>
                  <Typography
                    variant="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    {amount > 0 ? `US$${estimatedTaxes.toFixed(2)}` : "-"}
                  </Typography>
                </Box>

                <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                  <Typography
                    variant="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    Total
                  </Typography>
                  <Typography
                    variant="s1"
                    fontWeight="fontWeightSemiBold"
                    color="text.primary"
                  >
                    {amount > 0 ? `US$${total.toFixed(2)}` : "-"}
                  </Typography>
                </Box>
              </Box>
            </Box>

            <Divider sx={{ borderColor: "divider" }} />

            {/* Billed To - Always show */}
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
                  {/* <img src="/icons/settings/credit_card.png" alt="info" style={{  height: '20px' }} /> */}
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
                {/* <Button variant="outlined" sx={{ marginRight: '10px', width: '20%' }}>Add Card</Button> */}
                <Button
                  variant="outlined"
                  color="primary"
                  sx={{
                    width: "30%",
                  }}
                  onClick={handleAutoRechargeClick}
                >
                  Update Card
                </Button>
                {/* </Box> */}
              </Box>
            </Box>

            <Divider sx={{ borderColor: "divider" }} />

            {/* Action Buttons */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "end",
                gap: theme.spacing(1.5),
                mt: theme.spacing(2),
              }}
            >
              <Button
                variant="outlined"
                sx={{ width: "50%" }}
                onClick={() => {
                  trackEvent(Events.purchaseCancelled);
                  closeAndClear();
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
                loading={isLoading}
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
                disabled={!amount || amount <= 0 || !last4digits}
              >
                <Typography
                  variant="s2"
                  fontWeight={"fontWeightMedium"}
                  color={"white"}
                >
                  Purchase
                </Typography>
              </LoadingButton>
            </Box>
          </Box>
        </form>
      </Box>
    </Modal>
  );
};

AddFundsModal.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
  last4digits: PropType.string,
  handleAutoRechargeClick: PropType.func,
};

export default AddFundsModal;
