import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  IconButton,
  Typography,
  Grid,
  Box,
  Button,
  Paper,
  List,
  ListItem,
  ListItemText,
  useTheme,
  Chip,
  ListItemIcon,
  CircularProgress,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "../iconify";
import { Events, trackEvent } from "src/utils/Mixpanel";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { LoadingButton } from "@mui/lab";
import { useNavigate } from "react-router";
import logger from "src/utils/logger";
import { formatFeatures } from "src/utils/planFormatters";
import { usePlansAndAddons } from "src/hooks/use-plans-and-addons";

const PLAN_TYPES = {
  FREE: "free",
  PRO: "payg",
  CUSTOM: "custom",
};

const planData = {
  [PLAN_TYPES.FREE]: {
    name: "Free plan",
    icon: "circle",
    iconColor: "#ff6384", // Pink/red color based on the image
    tagline: "For individuals and early stage start-ups",
    price: 0,
    isCurrentPlan: true,
    footerText: "Advanced features with higher limits and SLAs",
  },
  [PLAN_TYPES.PRO]: {
    name: "Pay-as-you-go",
    icon: "circle",
    iconColor: "divider", // Gray color based on the image
    tagline: "For fast growing organizations",
    isCurrentPlan: false,
    includes: PLAN_TYPES.FREE,
    includesLowerTier: true,
    footerText: "Advanced features with higher limits and SLAs",
  },
  [PLAN_TYPES.CUSTOM]: {
    name: "Custom plan",
    icon: "diamond",
    iconColor: "#9c27b0", // Purple color based on the image
    tagline: "For large organization",
    price: "",
    isCurrentPlan: false,
    includesLowerTier: true,
    includes: "Pro",
    footerText: "Premium features with higher usage limit",
    hasContactButton: true,
  },
};

function PlanCard({
  name,
  icon,
  tagline,
  price,
  plan,
  includes,
  includesLowerTier,
  features,
  theme,
  hasContactButton,
  currentPlan,
  redirectToPlan,
}) {
  const [isLoadingButton, setIsLoadingButton] = useState(false);
  const isPaygPlan = plan === PLAN_TYPES.PRO;

  const handleContactSales = () => {
    trackEvent(Events.contactSalesClicked);
    window.open(
      "https://meetings.hubspot.com/salil-kolhe/help-futureagi-app?uuid=3b9c0e31-9f37-4d63-ad15-88190748204a",
    );
  };

  const handleUpgradeClick = async () => {
    setIsLoadingButton(true);
    try {
      const response = await axios.post(endpoints.settings.v2.upgradeToPayg);
      const checkoutUrl = response?.data?.result?.checkout_url;

      if (checkoutUrl) {
        window.location.href = checkoutUrl;
      } else {
        enqueueSnackbar("Failed to create checkout session", {
          variant: "error",
        });
      }
    } catch (err) {
      logger.error("Failed to create checkout session:", err);
      enqueueSnackbar("Failed to create checkout session", {
        variant: "error",
      });
    } finally {
      setIsLoadingButton(false);
    }
  };

  return (
    <Paper
      elevation={0}
      sx={{
        border: "1px solid",
        borderColor: "primary.lighter",
        p: 2,
        height: "100%",
        borderRadius: "12px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{ display: "flex", flexDirection: "column", alignItems: "start" }}
      >
        <Box
          component="img"
          alt="star"
          src={`/assets/rateLimit/${icon}.svg`}
          sx={{ width: 40, minWidth: 40 }}
        />

        <Box>
          <Typography
            variant="subtitle1"
            mt={0.5}
            fontSize={"16px"}
            fontWeight="600"
            sx={{ minWidth: "fit-content", display: "flex", gap: 1 }}
          >
            {name}
            {plan == currentPlan && (
              <Chip
                variant="soft"
                label="Your current plan"
                color="success"
                sx={{
                  height: "20px",
                  // padding: "2px 0px",
                  borderRadius: "4px",
                  // background: theme?.palette?.purple?.o10,
                  // color: theme?.palette?.purple[500],
                }}
              />
            )}
          </Typography>

          {tagline && (
            <Typography
              variant="caption"
              sx={{
                background: `linear-gradient(to right, ${theme.palette.primary.main}, ${theme.palette.pink[500]})`,
                WebkitBackgroundClip: "text",
                color: "transparent",
                fontSize: "12px",
                fontWeight: 400,
              }}
            >
              {tagline}
            </Typography>
          )}
        </Box>
      </Box>
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider", pb: 1 }}>
        {plan != PLAN_TYPES.CUSTOM && !isPaygPlan && (
          <Typography
            variant="h5"
            sx={{ fontSize: "16px" }}
            color={"text.primary"}
            fontWeight={600}
          >
            ${price}
            <Typography
              component="span"
              variant="body2"
              sx={{ fontSize: "12px" }}
              fontWeight={400}
              color="text.primary"
            >
              /monthly
            </Typography>
          </Typography>
        )}

        {isPaygPlan && (
          <Box>
            <Typography
              variant="h5"
              sx={{ fontSize: "16px" }}
              color={"text.primary"}
              fontWeight={600}
            >
              Usage-based
            </Typography>
            <Typography
              variant="body2"
              sx={{ fontSize: "12px" }}
              fontWeight={400}
              color="text.primary"
            >
              No monthly platform fee
            </Typography>
          </Box>
        )}

        {plan == PLAN_TYPES.CUSTOM && (
          <Typography
            variant="subtitle1"
            mt={0.5}
            fontSize={"16px"}
            color={"text.primary"}
            fontWeight={600}
          >
            Custom
          </Typography>
        )}
      </Box>

      {includesLowerTier && (
        <Typography
          variant="subtitle2"
          fontSize={"14px"}
          color={"text.primary"}
          fontWeight={600}
          sx={{ mt: 1 }}
        >
          Everything in {includes}, Plus:
        </Typography>
      )}

      <Box>
        <List dense>
          {features?.length > 0 &&
            features.map((feature, index) => (
              <ListItem sx={{ gap: 1 }} key={index} disableGutters>
                <ListItemIcon sx={{ minWidth: "16px", margin: "0px" }}>
                  <Iconify
                    icon="qlementine-icons:check-tick-16"
                    color="success.main"
                  />
                </ListItemIcon>
                <ListItemText
                  primary={feature}
                  sx={{
                    fontSize: "12px",
                    color: "text.primary",
                    "& .MuiTypography-root": {
                      fontWeight: 400,
                    },
                  }}
                />
              </ListItem>
            ))}
        </List>
      </Box>

      {hasContactButton && (
        <Button
          variant="contained"
          fullWidth
          color="primary"
          onClick={handleContactSales}
          sx={{
            mt: 3,
            borderRadius: "8px",
          }}
        >
          Contact us
        </Button>
      )}

      {currentPlan == PLAN_TYPES.FREE && plan == PLAN_TYPES.PRO && (
        <LoadingButton
          variant="contained"
          fullWidth
          color="primary"
          onClick={handleUpgradeClick}
          loading={isLoadingButton}
          sx={{
            mt: 3,
            borderRadius: "8px",
          }}
        >
          Upgrade to PAYG
        </LoadingButton>
      )}

      {((currentPlan == PLAN_TYPES.FREE && plan == PLAN_TYPES.PRO) ||
        currentPlan == PLAN_TYPES.PRO) && (
        <Typography
          component="div"
          fontSize={"10px"}
          sx={{
            mt: 0.5,
          }}
        >
          Pay-as-you-go unlocks higher limits and usage-based billing
          <Typography
            component="span"
            fontSize={"10px"}
            fontWeight={500}
            onClick={() => redirectToPlan()}
            sx={{ textDecoration: "underline", cursor: "pointer" }}
          >
            check annual plans
          </Typography>
        </Typography>
      )}
    </Paper>
  );
}

PlanCard.propTypes = {
  name: PropTypes.string,
  icon: PropTypes.string,
  tagline: PropTypes.string,
  price: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  plan: PropTypes.string,
  includes: PropTypes.string,
  includesLowerTier: PropTypes.bool,
  features: PropTypes.array,
  currentPlan: PropTypes.string,
  redirectToPlan: PropTypes.func,
  theme: PropTypes.object,
  hasContactButton: PropTypes.bool,
};

const PricingDialog = ({ open, onClose, title, description }) => {
  const theme = useTheme();
  const navigate = useNavigate();

  const { data: result, isLoading: plansLoading } = usePlansAndAddons(open);

  const plansByKey = [
    ...(result?.tiers || []),
    ...(result?.addons || []),
  ].reduce((acc, plan) => {
    acc[plan.key] = plan;
    return acc;
  }, {});

  const currentPlan = result?.current_plan || PLAN_TYPES.FREE;
  const data = {
    ...plansByKey,
    [PLAN_TYPES.CUSTOM]:
      result?.customDetails || plansByKey.enterprise || plansByKey.scale,
  };
  const businessMonthlyPrice =
    plansByKey[PLAN_TYPES.PRO]?.platform_fee_monthly || 0;

  const redirectToPlan = () => {
    onClose();
    navigate("/dashboard/settings/pricing");
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      PaperProps={{
        sx: {
          borderRadius: "12px",
          width: "100%",
          minHeight: "fit-content",
          position: "fixed",
          maxWidth: 950,
          pb: 2,
        },
      }}
    >
      <DialogTitle sx={{ display: "flex", flexDirection: "column", pb: 0 }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "start",
          }}
        >
          <Typography
            variant="body2"
            fontSize="18px"
            color="text.primary"
            fontWeight="600"
          >
            {title}
          </Typography>
          <IconButton
            onClick={onClose}
            size="small"
            aria-label="close"
            sx={{
              padding: "0px",
              margin: "0px",
              color: "text.primary",
            }}
          >
            <Iconify icon="oui:cross" />
          </IconButton>
        </Box>
        <Typography
          variant="body2"
          fontSize={"14px"}
          color="text.secondary"
          sx={{ mb: 1 }}
        >
          {description}
        </Typography>
      </DialogTitle>

      {plansLoading ? (
        <Box
          sx={{
            p: 2,
            minHeight: "30.4rem",
            borderRadius: "12px",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <CircularProgress />
        </Box>
      ) : (
        <DialogContent>
          <Grid container spacing={1.5}>
            <Grid item xs={12} md={4}>
              <PlanCard
                {...planData[PLAN_TYPES?.FREE]}
                features={formatFeatures(data?.[PLAN_TYPES?.FREE]?.features)}
                currentPlan={currentPlan}
                plan={PLAN_TYPES?.FREE}
                redirectToPlan={redirectToPlan}
                icon="star"
                theme={theme}
              />
            </Grid>

            <Grid item xs={12} md={4}>
              <PlanCard
                {...planData[PLAN_TYPES?.PRO]}
                price={businessMonthlyPrice}
                features={formatFeatures(data?.[PLAN_TYPES?.PRO]?.features)}
                currentPlan={currentPlan}
                plan={PLAN_TYPES?.PRO}
                redirectToPlan={redirectToPlan}
                icon="target"
                theme={theme}
              />
            </Grid>

            <Grid item xs={12} md={4}>
              <PlanCard
                {...planData[PLAN_TYPES?.CUSTOM]}
                features={formatFeatures(data?.[PLAN_TYPES?.CUSTOM]?.features)}
                currentPlan={currentPlan}
                plan={PLAN_TYPES?.CUSTOM}
                redirectToPlan={redirectToPlan}
                icon="daimond"
                theme={theme}
              />
            </Grid>
          </Grid>
        </DialogContent>
      )}
    </Dialog>
  );
};

PricingDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  title: PropTypes.string,
  description: PropTypes.string,
};

export default PricingDialog;
