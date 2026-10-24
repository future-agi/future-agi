import React, { useEffect } from "react";
import PageHeader from "src/components/page-header";
import { Helmet } from "react-helmet-async";
import Typography from "@mui/material/Typography";
import {
  Box,
  Button,
  Divider,
  Stack,
  useTheme,
  Switch,
  Skeleton,
} from "@mui/material";
import BillingInfoModal from "./BillingInfoModal";
import { useState } from "react";
import InvoiceHistoryTable from "./InvoiceHistoryTable";
import AddFundsModal from "./AddFundsModal";
import { endpoints } from "src/utils/axios";
import axios from "src/utils/axios";
import { trackEvent, Events } from "src/utils/Mixpanel";
import EditAutoReloadSettingsModal from "./EditAutoReloadSettingsModal";
import { useLocation } from "react-router-dom"; // Add useLocation import
import { enqueueSnackbar } from "notistack";
import { CreditCardIcon } from "./IconComponents";

import { stripePromise } from "./stripeVariables";
import Iconify from "src/components/iconify";
import logger from "src/utils/logger";
import { useMutation, useQuery } from "@tanstack/react-query";

const BillingPage = () => {
  const [openBillingInfoModal, setOpenBillingInfoModal] = useState(false);
  const [openAddFundsModal, setOpenAddFundsModal] = useState(false);
  const [openEditSettingsModal, setOpenEditSettingsModal] = useState(false);
  const [walletBalance, setWalletBalance] = useState(0);
  const [last4digits, setLast4digits] = useState("");
  const [invoicesData, setInvoicesData] = useState([]);
  const [refreshBillingPage, setRefreshBillingPage] = useState(false);
  const [hasAutoReloadSettings, setHasAutoReloadSettings] = useState(false);

  const [paginationModel] = useState({
    page: 0,
    pageSize: 10,
  });
  const [billingDetails, setBillingDetails] = useState(null);
  const [autoReloadEnabled, setAutoReloadEnabled] = useState(false);
  const [walletTopupAmount, setWalletTopupAmount] = useState(0);
  const [topupThreshold, setTopupThreshold] = useState(0);
  const location = useLocation(); // Get the current location
  const theme = useTheme();

  // useQuery for wallet balance
  const { data: walletBalanceData, isLoading: isWalletBalanceLoadingQuery } =
    useQuery({
      queryKey: ["wallet-balance"],
      queryFn: () => axios.get(endpoints.stripe.getWalletBalance),
      select: (data) => Number(data.data.wallet_balance).toFixed(2),
      enabled: true,
    });

  // useQuery for last4digits
  const { data: last4DigitsData, isLoading: isLast4DigitsLoadingQuery } =
    useQuery({
      queryKey: ["last4-digits"],
      queryFn: () => axios.get(endpoints.stripe.getLast4Digits),
      select: (data) => data?.data?.result?.last4,
      enabled: true,
    });

  // useQuery for auto reload settings
  const {
    data: autoReloadSettingsData,
    isLoading: isAutoReloadSettingsLoadingQuery,
  } = useQuery({
    queryKey: ["auto-reload-settings", refreshBillingPage],
    queryFn: () => axios.get(endpoints.stripe.getAutoReloadSettings),
    select: (data) => data.data.data,
    enabled: true,
  });

  // useQuery for invoice history
  const { data: invoicesDataQuery } = useQuery({
    queryKey: [
      "invoice-history",
      paginationModel.page,
      paginationModel.pageSize,
    ],
    queryFn: () =>
      axios.get(endpoints.stripe.getCustomerInvoices, {
        params: {
          page: paginationModel.page + 1,
          page_size: paginationModel.pageSize,
        },
      }),
    select: (data) => data.data,
    enabled: true,
  });

  // useMutation for auto reload toggle
  const { mutate: updateAutoReloadSettings, isPending: isUpdatingAutoReload } =
    useMutation({
      mutationFn: (data) =>
        axios.post(endpoints.stripe.updateAutoReloadSettings, data),
      onSuccess: (data, variables) => {
        const newState = variables.autoreload_enabled;
        setAutoReloadEnabled(newState);
        trackEvent(Events.autoReloadClicked);
        enqueueSnackbar(
          newState ? "Auto reload enabled" : "Auto reload disabled",
          {
            variant: newState ? "success" : "warning",
          },
        );
        setRefreshBillingPage((prev) => !prev);
      },
      onError: (error) => {
        logger.error("Error updating auto reload settings:", error);
        enqueueSnackbar("Failed to update auto reload settings", {
          variant: "error",
        });
      },
    });

  // useMutation for create auto recharge session
  const { mutate: createAutoRechargeSession } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.stripe.createAutoRechargeSession, data),
    onSuccess: async (data) => {
      try {
        const stripe = await stripePromise;
        if (!stripe) {
          enqueueSnackbar("Failed to create checkout session", {
            variant: "error",
          });
          return;
        }
        const { error } = await stripe.redirectToCheckout({
          sessionId: data.data.sessionId,
        });

        if (error) {
          logger.error("Error during checkout:", error);
        }
      } catch (err) {
        logger.error("Failed to create checkout session:", err);
      }
    },
    onError: (error) => {
      logger.error("Failed to create checkout session:", error);
    },
  });

  const handleGetBillingDetails = async () => {
    try {
      const response = await axios.get(endpoints.stripe.getBillingDetails);
      setBillingDetails(response.data.billing_info);
    } catch (error) {
      enqueueSnackbar(
        "Failed to fetch billing details. Please refresh the page.",
        { variant: "error" },
      );
    }
  };

  const handleAutoRechargeClick = async () => {
    createAutoRechargeSession({ amount: 100 });
  };

  useEffect(() => {
    handleGetBillingDetails();
  }, [openBillingInfoModal]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("add_funds_successful")) {
      enqueueSnackbar("Your credit balance has been updated", {
        variant: "success",
        autoHideDuration: 2000,
      });
    }
    if (params.get("add_funds_failed")) {
      enqueueSnackbar("Your add funds transaction was unsuccessful", {
        variant: "error",
        autoHideDuration: 2000,
      });
    }
    if (params.get("update_card_successful")) {
      enqueueSnackbar("Your card has been updated", {
        variant: "success",
        autoHideDuration: 2000,
      });
    }
    if (params.get("update_card_failed")) {
      enqueueSnackbar("The card updation was unsuccessful", {
        variant: "error",
        autoHideDuration: 2000,
      });
    }

    const newUrl = location.pathname; // Get the current path without query params
    window.history.replaceState({}, document.title, newUrl); // Update the URL
  }, [location.pathname, location.search]);

  // Update state when query data changes
  useEffect(() => {
    if (walletBalanceData) {
      setWalletBalance(walletBalanceData);
    }
  }, [walletBalanceData]);

  useEffect(() => {
    if (last4DigitsData) {
      setLast4digits(last4DigitsData);
    }
  }, [last4DigitsData]);

  useEffect(() => {
    if (autoReloadSettingsData) {
      const {
        autoreload_enabled,
        autoreload_wallet_amount,
        autoreload_wallet_threshold,
      } = autoReloadSettingsData;
      setAutoReloadEnabled(autoreload_enabled);
      setWalletTopupAmount(autoreload_wallet_amount);
      setTopupThreshold(autoreload_wallet_threshold);
      setHasAutoReloadSettings(
        Boolean(autoreload_wallet_amount && autoreload_wallet_threshold),
      );
    }
  }, [autoReloadSettingsData]);

  useEffect(() => {
    if (invoicesDataQuery) {
      setInvoicesData(invoicesDataQuery);
    }
  }, [invoicesDataQuery]);

  const handleAutoReloadToggle = async (newState) => {
    updateAutoReloadSettings({
      autoreload_enabled: newState,
      autoreload_walletamount: walletTopupAmount,
      autoreload_walletthreshold: topupThreshold,
    });
  };

  const constructAddress = (billingDetails) => {
    let address = "";
    if (billingDetails.billingAddress1) {
      address += billingDetails.billingAddress1;
    }
    if (billingDetails.billingAddress2) {
      address += ", " + billingDetails.billingAddress2;
    }
    return address;
  };

  return (
    <>
      <Helmet>
        <title>Billing</title>
      </Helmet>
      <Box
        sx={{ display: "flex", flexDirection: "column", gap: theme.spacing(2) }}
      >
        <PageHeader
          title="Billing details"
          description="Manage your billing details"
          sx={{ mb: 0 }}
        />
        <Box
          sx={{
            borderRadius: theme.spacing(1),
            border: "1px solid",
            borderColor: "divider",
            padding: theme.spacing(1.5),
            backgroundColor: "background.paper",
            height: "250px",
          }}
        >
          <Typography
            typography="m3"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            Credit Balance
          </Typography>
          <Box
            sx={{
              display: "flex",
              marginTop: theme.spacing(1.5),
              flexDirection: "row",
              gap: theme.spacing(2.5),
            }}
          >
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: theme.spacing(1),
                padding: theme.spacing(2),
                backgroundColor: "background.neutral",
                width: "25%",
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
                height: "179px",
                gap: theme.spacing(2),
              }}
            >
              <Typography
                typography="m2"
                fontWeight="fontWeightMedium"
                color="text.primary"
                sx={{
                  textAlign: "center",
                }}
              >
                Remaining Balance
              </Typography>
              <Divider sx={{ borderColor: "divider" }} />
              <Typography
                typography="l1"
                fontWeight="fontWeightSemiBold"
                color="text.primary"
                sx={{
                  textAlign: "center",
                }}
              >
                {isWalletBalanceLoadingQuery ? (
                  <Skeleton
                    variant="text"
                    width={80}
                    height={50}
                    sx={{ margin: "0 auto" }}
                  />
                ) : (
                  `$${walletBalance}`
                )}
              </Typography>
            </Box>
            <Box
              sx={{
                width: "75%",
                height: "179px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
              }}
            >
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: theme.spacing(1),
                  backgroundColor: "background.paper",
                }}
              >
                <Box
                  sx={{
                    marginTop: theme.spacing(2),
                    marginLeft: theme.spacing(2.5),
                    marginBottom: theme.spacing(1.5),
                  }}
                >
                  <Typography
                    typography="m3"
                    fontWeight="fontWeightMedium"
                    color="text.primary"
                  >
                    Billed To
                  </Typography>
                </Box>
                <Divider sx={{ borderColor: "divider" }} />
                <Box
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: theme.spacing(2.5),
                    gap: theme.spacing(2),
                  }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      width: "100%",
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: theme.spacing(0.5),
                      padding: theme.spacing(1),
                      paddingLeft: theme.spacing(2),
                    }}
                  >
                    <CreditCardIcon
                      width={20}
                      height={20}
                      fill="none"
                      stroke="gray"
                      strokeWidth={1.5}
                    />
                    <Typography
                      typography="s1"
                      fontWeight="fontWeightMedium"
                      color="text.disabled"
                      sx={{
                        marginRight: theme.spacing(1.25),
                        marginLeft: theme.spacing(1),
                      }}
                    >
                      {isLast4DigitsLoadingQuery ? (
                        <Skeleton variant="text" width={150} height={20} />
                      ) : last4digits ? (
                        `************${last4digits}`
                      ) : (
                        "N/A"
                      )}
                    </Typography>
                  </Box>
                  <Box
                    sx={{ display: "flex", gap: theme.spacing(2), flexGrow: 1 }}
                  >
                    <Box
                      sx={{
                        flexGrow: 1,
                        display: "flex",
                        justifyContent: "center",
                        minWidth: "152px",
                      }}
                    >
                      {isLast4DigitsLoadingQuery ? (
                        <Skeleton
                          variant="rectangular"
                          width={152}
                          height={36}
                          sx={{ borderRadius: 1 }}
                        />
                      ) : (
                        <Button
                          variant={last4digits ? "outlined" : "contained"}
                          color={last4digits ? "inherit" : "primary"}
                          size="medium"
                          sx={{
                            ...(last4digits && {
                              borderColor: "border.default",
                              color: "text.primary",
                              "&:hover": {
                                borderColor: "text.secondary",
                                backgroundColor: "action.hover",
                              },
                            }),
                            "&:disabled": {
                              color: "text.disabled",
                              backgroundColor: "action.hover",
                            },
                          }}
                          startIcon={
                            last4digits ? (
                              <Iconify
                                width={20}
                                icon="solar:refresh-linear"
                                color="text.primary"
                              />
                            ) : (
                              <Iconify
                                width={20}
                                icon="solar:add-linear"
                                color="primary.contrastText"
                              />
                            )
                          }
                          onClick={() => {
                            trackEvent(Events.updateCardClicked);
                            handleAutoRechargeClick();
                          }}
                          // disabled={!last4digits}
                        >
                          <Typography
                            typography="s2"
                            fontWeight="fontWeightMedium"
                            color={
                              last4digits
                                ? "text.primary"
                                : "primary.contrastText"
                            }
                          >
                            {last4digits ? "Update Card" : "Add Card"}
                          </Typography>
                        </Button>
                      )}
                    </Box>

                    <Button
                      variant="contained"
                      color="primary"
                      sx={{
                        flexGrow: 1,
                        minWidth: "152px",
                        "&:disabled": {
                          color: "text.disabled",
                          backgroundColor: "action.hover",
                        },
                      }}
                      startIcon={
                        <Iconify
                          width={20}
                          icon="solar:add-linear"
                          color="primary.contrastText"
                        />
                      }
                      onClick={() => {
                        trackEvent(Events.addFundsClicked);
                        setOpenAddFundsModal(true);
                      }}
                      disabled={!last4digits}
                    >
                      <Typography
                        typography="s1"
                        fontWeight="fontWeightMedium"
                        color="primary.contrastText"
                      >
                        Add Funds
                      </Typography>
                    </Button>
                  </Box>

                  <AddFundsModal
                    open={openAddFundsModal}
                    onClose={() => setOpenAddFundsModal(false)}
                    last4digits={last4digits}
                    handleAutoRechargeClick={handleAutoRechargeClick}
                  />
                </Box>
              </Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  backgroundColor: "background.neutral",
                  paddingX: theme.spacing(1.5),
                  paddingY: theme.spacing(0.5),
                  borderRadius: theme.spacing(1),
                }}
              >
                {isAutoReloadSettingsLoadingQuery && !hasAutoReloadSettings && (
                  <Skeleton variant="rectangular" width={600} height={18} />
                )}
                {!isAutoReloadSettingsLoadingQuery &&
                  !hasAutoReloadSettings && (
                    <>
                      <Iconify
                        icon="solar:danger-circle-linear"
                        color="status.danger.fg"
                        sx={{
                          width: "20px",
                          height: "20px",
                        }}
                      />
                      <Typography
                        typography="s1"
                        fontWeight="fontWeightRegular"
                        color="text.primary"
                        sx={{ marginLeft: "8px" }}
                      >
                        Auto reload is not configured. Please configure auto
                        reload settings to avoid API interruptions
                      </Typography>
                    </>
                  )}
                {hasAutoReloadSettings && (
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: theme.spacing(1),
                    }}
                  >
                    <Switch
                      checked={autoReloadEnabled}
                      size="small"
                      onChange={(event) =>
                        handleAutoReloadToggle(event.target.checked)
                      }
                      color="success"
                      disabled={isUpdatingAutoReload}
                    />
                    {isAutoReloadSettingsLoadingQuery ? (
                      <Skeleton variant="text" width={600} height={20} />
                    ) : (
                      <Typography
                        typography="s1"
                        fontWeight="fontWeightRegular"
                        color="text.primary"
                      >
                        {autoReloadEnabled
                          ? `Auto reload is enabled. We will reload to $${walletTopupAmount} when the balance reaches $${topupThreshold}`
                          : "Auto reload is disabled. You could experience potential API interruptions when you use up your credits"}
                      </Typography>
                    )}
                  </Box>
                )}
                <Button
                  variant="text"
                  size="small"
                  sx={{ marginLeft: theme.spacing(1) }}
                  onClick={() => {
                    trackEvent(Events.editSettingsClicked);
                    setOpenEditSettingsModal(true);
                  }}
                >
                  {hasAutoReloadSettings
                    ? "Edit Settings"
                    : "Configure Settings"}
                </Button>
              </Box>
            </Box>
          </Box>
        </Box>
        <Box
          sx={{
            borderRadius: theme.spacing(1),
            border: "1px solid",
            borderColor: "divider",
            padding: theme.spacing(1.5),
            backgroundColor: "background.paper",
            paddingBottom: theme.spacing(3),
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
              typography="m3"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              Billing Information
            </Typography>
            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              Your billing information will be displayed on all your invoices
              and billing communication
            </Typography>
          </Box>
          <Box
            sx={{
              display: "flex",
              marginY: theme.spacing(3),
              width: "100%",
            }}
          >
            <Stack
              direction="row"
              spacing={2}
              width="100%"
              justifyContent="space-between"
            >
              <Box sx={{ width: "14%" }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ color: "text.primary", marginBottom: theme.spacing(1) }}
                >
                  Billed Contact Name
                </Typography>
                <Typography
                  variant="body1"
                  fontWeight="fontWeightMedium"
                  sx={{ color: "text.primary", marginTop: theme.spacing(1) }}
                >
                  {billingDetails?.name ? billingDetails.name : "-"}
                </Typography>
              </Box>
              <Box sx={{ width: "16%" }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ color: "text.primary", marginBottom: theme.spacing(1) }}
                >
                  Billed Email ID
                </Typography>
                <Typography
                  variant="body1"
                  fontWeight="fontWeightMedium"
                  sx={{ color: "text.primary", marginTop: theme.spacing(1) }}
                >
                  {billingDetails?.email ? billingDetails.email : "-"}
                </Typography>
              </Box>
              <Box sx={{ width: "20%" }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ color: "text.primary", marginBottom: theme.spacing(1) }}
                >
                  Billing Address 1
                </Typography>
                <Typography
                  variant="body1"
                  fontWeight="fontWeightMedium"
                  sx={{ color: "text.primary", marginTop: theme.spacing(1) }}
                >
                  {billingDetails?.billingAddress1
                    ? constructAddress(billingDetails)
                    : "-"}
                </Typography>
              </Box>
              <Box sx={{ width: "14%" }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ color: "text.primary", marginBottom: theme.spacing(1) }}
                >
                  Country
                </Typography>
                <Typography
                  variant="body1"
                  fontWeight="fontWeightMedium"
                  sx={{ color: "text.primary", marginTop: theme.spacing(1) }}
                >
                  {billingDetails?.country ? billingDetails.country : "-"}
                </Typography>
              </Box>
              <Box sx={{ width: "12%" }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ color: "text.primary", marginBottom: theme.spacing(1) }}
                >
                  States
                </Typography>
                <Typography
                  variant="body1"
                  fontWeight="fontWeightMedium"
                  sx={{ color: "text.primary", marginTop: theme.spacing(1) }}
                >
                  {billingDetails?.state ? billingDetails.state : "-"}
                </Typography>
              </Box>
              <Box sx={{ width: "12%" }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ color: "text.primary", marginBottom: theme.spacing(1) }}
                >
                  Postal Code
                </Typography>
                <Typography
                  variant="body1"
                  fontWeight="fontWeightMedium"
                  sx={{ color: "text.primary", marginTop: theme.spacing(1) }}
                >
                  {billingDetails?.postalCode ? billingDetails.postalCode : "-"}
                </Typography>
              </Box>
              <Box sx={{ width: "12%" }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ color: "text.primary", marginBottom: theme.spacing(1) }}
                >
                  Tax ID
                </Typography>
                <Typography
                  variant="body1"
                  fontWeight="fontWeightMedium"
                  sx={{ color: "text.primary", marginTop: theme.spacing(1) }}
                >
                  {billingDetails?.taxId ? billingDetails.taxId : "-"}
                </Typography>
              </Box>
            </Stack>
          </Box>
          <Button
            variant="contained"
            color="primary"
            startIcon={
              billingDetails?.name ? (
                <Iconify icon="solar:pen-linear" color="primary.contrastText" />
              ) : (
                <Iconify
                  width={20}
                  icon="solar:add-linear"
                  color="primary.contrastText"
                />
              )
            }
            onClick={() => {
              trackEvent(Events.editBillingClicked);
              setOpenBillingInfoModal(true);
            }}
          >
            {billingDetails?.name
              ? "Edit Billing Details"
              : "Add Billing Details"}
          </Button>
        </Box>

        <Box
          sx={{
            borderRadius: theme.spacing(1),
            border: "1px solid",
            borderColor: "divider",
            padding: theme.spacing(1.5),
            backgroundColor: "background.paper",
            paddingBottom: theme.spacing(3),
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
              typography="m3"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              Invoice History
            </Typography>

            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              Your complete record of your billing and payment history
            </Typography>
          </Box>

          <InvoiceHistoryTable userData={invoicesData} />
        </Box>
      </Box>
      <BillingInfoModal
        open={openBillingInfoModal}
        onClose={() => setOpenBillingInfoModal(false)}
      />
      <EditAutoReloadSettingsModal
        open={openEditSettingsModal}
        onClose={() => setOpenEditSettingsModal(false)}
        handleAutoRechargeClick={handleAutoRechargeClick}
        last4digits={last4digits}
        walletTopupAmount={walletTopupAmount}
        topupThreshold={topupThreshold}
        autoReload={autoReloadEnabled}
        setRefreshBillingPage={setRefreshBillingPage}
      />
    </>
  );
};

export default BillingPage;
