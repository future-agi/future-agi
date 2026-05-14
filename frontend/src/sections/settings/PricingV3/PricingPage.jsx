/**
 * Plans & Pricing V3 — 2 tiers + add-ons (Phase 7b)
 *
 * Customer-facing positioning:
 * - 2 tiers: Free / Pay-as-you-go
 * - 3 add-ons: Boost ($250), Scale ($750), Enterprise ($2K)
 * - Feature comparison matrix
 * - Usage-based pricing tiers
 * - Live cost calculator
 */

import React, { useState, useCallback, useEffect } from "react";
import PropTypes from "prop-types";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Stack,
  Paper,
  Button,
  Chip,
  Divider,
  Switch,
  FormControlLabel,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  alpha,
  DialogContent,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";

import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { fCurrency } from "src/utils/format-number";
import {
  FEATURE_LABELS,
  ADDON_ICONS,
  ADDON_DESCRIPTIONS,
  getFeatureGroups,
  SKIP_FEATURE_PREFIXES,
} from "./constants";

function formatCompact(n) {
  if (n == null) return "—";
  if (n >= 1_000_000_000) return `${+(n / 1_000_000_000).toPrecision(3)}B`;
  if (n >= 1_000_000) return `${+(n / 1_000_000).toPrecision(3)}M`;
  if (n >= 10_000) return `${+(n / 1_000).toPrecision(3)}K`;
  return n.toLocaleString();
}

function getPricingDisplayUnit(dimKey, pricing) {
  return pricing.display_unit || dimKey;
}

function getPricingRateUnit(dimKey, pricing) {
  const displayUnit = getPricingDisplayUnit(dimKey, pricing);
  return dimKey === "storage" ? `${displayUnit}/mo` : displayUnit;
}

function getStorageBillingCopy(storagePricing) {
  const freeTier = storagePricing?.tiers?.find(
    (tier) => tier.price_per_unit === 0 && Number.isFinite(tier.up_to),
  );
  const paidTier = storagePricing?.tiers?.find(
    (tier) => Number.isFinite(tier.price_per_unit) && tier.price_per_unit > 0,
  );

  if (!freeTier || !paidTier) return null;

  const exampleOverage = 10;
  const exampleUsage = freeTier.up_to + exampleOverage;
  const exampleCost = paidTier.price_per_unit * exampleOverage;
  const unit = storagePricing.display_unit || "GB";

  return {
    allowance: `First ${formatCompact(freeTier.up_to)} ${unit} included each month.`,
    example: `Example: ${formatCompact(exampleUsage)} ${unit} uses ${formatCompact(exampleOverage)} extra ${unit}, billed at ${fCurrency(paidTier.price_per_unit)}/${unit}/mo for ${fCurrency(exampleCost)}/mo.`,
  };
}

// Constants imported from ./constants.js

// ── Tier Card (Free / PAYG) ────────────────────────────────────────────────

function TierCard({ tier, isCurrent, onUpgrade }) {
  const isFree = tier.key === "free";

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 3,
        flex: 1,
        borderRadius: 2,
        borderColor: isCurrent ? "primary.main" : "divider",
        borderWidth: isCurrent ? 2 : 1,
        position: "relative",
      }}
    >
      {isCurrent && (
        <Chip
          label="Current"
          size="small"
          color="primary"
          sx={{ position: "absolute", top: 12, right: 12 }}
        />
      )}

      <Typography variant="h6" fontWeight={700} mb={0.5}>
        {tier.display_name}
      </Typography>

      <Typography variant="body2" color="text.secondary" mb={2}>
        {isFree
          ? "Get started with core observability, evaluations, and dashboards. Usage capped at free tier limits."
          : "Everything in Free, with no usage caps. Pay only for what you use beyond the free allowance."}
      </Typography>

      {!isCurrent && (
        <Button
          variant={isFree ? "outlined" : "contained"}
          fullWidth
          onClick={() => onUpgrade(tier.key)}
        >
          {isFree ? "Switch to Free" : "Upgrade to PAYG"}
        </Button>
      )}
      {isCurrent && (
        <Button variant="outlined" fullWidth disabled>
          Current plan
        </Button>
      )}
    </Paper>
  );
}

TierCard.propTypes = {
  tier: PropTypes.shape({
    key: PropTypes.string,
    display_name: PropTypes.string,
  }).isRequired,
  isCurrent: PropTypes.bool,
  onUpgrade: PropTypes.func.isRequired,
};

// ── Add-on Card (Boost / Scale / Enterprise) ───────────────────────────────

const ADDON_ELIGIBLE_PLANS = ["payg", "boost", "scale", "enterprise"];

function AddonCard({
  addon,
  isCurrent,
  isIncluded,
  isAnnual,
  currentPlan,
  pendingCancel,
  cancelAt,
  onAdd,
  onRemove,
  onReinstate,
}) {
  const iconName = ADDON_ICONS[addon.key] || "mdi:star";
  const monthlyFee = addon.platform_fee_monthly;
  const annualFee = monthlyFee * 12 * 0.8; // 20% discount for annual
  const displayFee = isAnnual ? annualFee / 12 : monthlyFee;
  const canPurchase = ADDON_ELIGIBLE_PLANS.includes(currentPlan);
  const disabledReason =
    currentPlan === "custom"
      ? "Custom plans manage add-ons separately — contact support."
      : "Upgrade to Pay-as-you-go to enable add-ons.";

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 2.5,
        flex: 1,
        minHeight: 280,
        borderRadius: 2,
        borderColor: isCurrent ? "primary.main" : "divider",
        borderWidth: isCurrent ? 2 : 1,
        position: "relative",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {isCurrent && (
        <Chip
          label={pendingCancel ? "Cancelling" : "Active"}
          size="small"
          color={pendingCancel ? "warning" : "primary"}
          sx={{
            position: "absolute",
            top: 12,
            right: 12,
          }}
        />
      )}

      <Stack direction="row" alignItems="center" spacing={1.5} mb={1.5}>
        <Box
          sx={{
            width: 40,
            height: 40,
            borderRadius: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: "action.hover",
          }}
        >
          <Iconify icon={iconName} width={22} sx={{ color: "primary.main" }} />
        </Box>
        <Box>
          <Typography variant="subtitle1" fontWeight={700}>
            {addon.display_name}
          </Typography>
          <Stack direction="row" alignItems="baseline" spacing={0.5}>
            <Typography variant="h5" fontWeight={800}>
              {fCurrency(displayFee)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              /mo {isAnnual && "(billed annually)"}
            </Typography>
          </Stack>
        </Box>
      </Stack>

      {ADDON_DESCRIPTIONS[addon.key] && (
        <Box
          component="ul"
          sx={{
            m: 0,
            mb: 1.5,
            pl: 2.5,
            flexGrow: 1,
            listStyle: "disc",
            textAlign: "left",
            "& li::marker": { fontSize: "1.2em" },
          }}
        >
          {ADDON_DESCRIPTIONS[addon.key].map((item) => (
            <Typography
              key={item}
              component="li"
              variant="body2"
              color="text.secondary"
              sx={{ py: 0.3, lineHeight: 1.8 }}
            >
              {item}
            </Typography>
          ))}
        </Box>
      )}

      <Box sx={{ mt: "auto" }}>
        {isCurrent ? (
          pendingCancel ? (
            <Stack spacing={1}>
              <Typography variant="caption" color="warning.main">
                {cancelAt
                  ? `Inactive on ${new Date(cancelAt).toLocaleDateString()}`
                  : "Scheduled to cancel at period end"}
              </Typography>
              <Button
                variant="outlined"
                color="primary"
                size="small"
                fullWidth
                onClick={() => onReinstate(addon.key)}
              >
                Reinstate add-on
              </Button>
            </Stack>
          ) : (
            <Button
              variant="outlined"
              color="error"
              size="small"
              fullWidth
              onClick={() => onRemove(addon.key)}
            >
              Remove add-on
            </Button>
          )
        ) : isIncluded ? (
          <Button variant="outlined" size="small" fullWidth disabled>
            Included in your plan
          </Button>
        ) : !canPurchase ? (
          <Tooltip title={disabledReason}>
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                fullWidth
                disabled
              >
                Add {addon.display_name}
              </Button>
            </span>
          </Tooltip>
        ) : (
          <Button
            variant="contained"
            color="primary"
            size="small"
            fullWidth
            onClick={() => onAdd(addon.key)}
          >
            Add {addon.display_name}
          </Button>
        )}
      </Box>
    </Paper>
  );
}

AddonCard.propTypes = {
  addon: PropTypes.shape({
    key: PropTypes.string,
    display_name: PropTypes.string,
    platform_fee_monthly: PropTypes.number,
  }).isRequired,
  isCurrent: PropTypes.bool,
  isIncluded: PropTypes.bool,
  isAnnual: PropTypes.bool,
  currentPlan: PropTypes.string,
  pendingCancel: PropTypes.bool,
  cancelAt: PropTypes.string,
  onAdd: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onReinstate: PropTypes.func.isRequired,
};

// ── Boolean Feature List (shared by add/remove dialogs) ───────────────────

// FEATURE_LABELS imported from ./constants

function BooleanFeatureList({ features, isRemove }) {
  // Show boolean features that are true + numeric features that are > 0
  const displayFeatures = Object.entries(features || {})
    .filter(([key, v]) => {
      if (SKIP_FEATURE_PREFIXES.some((p) => key.startsWith(p))) return false;
      if (typeof v === "boolean") return v === true;
      if (typeof v === "number") return v > 0 && FEATURE_LABELS[key];
      return false;
    })
    .map(([key, v]) => ({
      key,
      label: FEATURE_LABELS[key] || key,
      value:
        typeof v === "number"
          ? v === -1
            ? "Unlimited"
            : formatCompact(v)
          : null,
    }));

  if (!displayFeatures.length) return null;

  const midpoint = Math.ceil(displayFeatures.length / 2);
  const leftCol = displayFeatures.slice(0, midpoint);
  const rightCol = displayFeatures.slice(midpoint);

  const renderCol = (items) => (
    <List dense disablePadding sx={{ flex: 1 }}>
      {items.map(({ key, label, value }) => (
        <ListItem key={key} disableGutters sx={{ py: 0.25 }}>
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify
              icon={isRemove ? "mdi:close" : "mdi:check"}
              width={18}
              sx={{ color: isRemove ? "error.main" : "success.main" }}
            />
          </ListItemIcon>
          <ListItemText
            primary={value ? `${label} (${value})` : label}
            primaryTypographyProps={{ variant: "body2" }}
          />
        </ListItem>
      ))}
    </List>
  );

  return (
    <>
      <Typography variant="body2" color="text.secondary" mb={1.5}>
        {isRemove
          ? "You\u2019ll lose access to these features at the end of your billing period:"
          : "You\u2019ll get access to these features:"}
      </Typography>
      <Stack direction="row" spacing={1}>
        {renderCol(leftCol)}
        {rightCol.length > 0 && renderCol(rightCol)}
      </Stack>
    </>
  );
}

BooleanFeatureList.propTypes = {
  features: PropTypes.object,
  isRemove: PropTypes.bool,
};

// ── Feature Matrix ─────────────────────────────────────────────────────────

// FEATURE_GROUPS built from constants.js via getFeatureGroups(formatCompact)
const FEATURE_GROUPS = getFeatureGroups(formatCompact);

function formatNumericEntitlement(val) {
  if (val == null) return "—";
  if (val === -1) return "Unlimited";
  if (val === 0) return "—";
  return formatCompact(val);
}

function FeatureMatrix({ plansData }) {
  const plans = ["free", "payg", "boost", "scale", "enterprise"];
  const planLabels = {
    free: "Free",
    payg: "PAYG",
    boost: "+Boost",
    scale: "+Scale",
    enterprise: "+Enterprise",
  };

  return (
    <TableContainer
      component={Paper}
      variant="outlined"
      sx={{ borderRadius: 2, "& td, & th": { borderBottomStyle: "solid" } }}
    >
      <Table size="small">
        <TableHead>
          <TableRow sx={{ bgcolor: "background.neutral" }}>
            <TableCell
              sx={{ fontWeight: 700, minWidth: 200, py: 2, fontSize: "1rem" }}
            >
              Feature
            </TableCell>
            {plans.map((p) => (
              <TableCell
                key={p}
                align="center"
                sx={{ fontWeight: 700, minWidth: 100, py: 2, fontSize: "1rem" }}
              >
                {planLabels[p]}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {FEATURE_GROUPS.map((group) => (
            <React.Fragment key={group.name}>
              <TableRow>
                <TableCell
                  colSpan={6}
                  sx={{
                    bgcolor: "action.hover",
                    fontWeight: 700,
                    fontSize: "0.75rem",
                    textTransform: "uppercase",
                    letterSpacing: 0.5,
                    py: 1,
                  }}
                >
                  {group.name}
                </TableCell>
              </TableRow>
              {group.features.map((feat) => (
                <TableRow key={feat.key || feat.label} hover>
                  <TableCell sx={{ fontSize: "0.813rem" }}>
                    {feat.label}
                  </TableCell>
                  {plans.map((p) => {
                    const planFeatures = plansData?.[p]?.features || {};
                    const val = planFeatures[feat.key];

                    if (feat.allPlans) {
                      return (
                        <TableCell key={p} align="center">
                          <Iconify
                            icon="mdi:check"
                            width={20}
                            sx={{ color: "success.main" }}
                          />
                        </TableCell>
                      );
                    }

                    if (feat.type === "bool") {
                      return (
                        <TableCell key={p} align="center">
                          {val ? (
                            <Iconify
                              icon="mdi:check"
                              width={20}
                              sx={{ color: "success.main" }}
                            />
                          ) : (
                            <Iconify
                              icon="mdi:close"
                              width={16}
                              sx={{ color: "error.main", opacity: 0.7 }}
                            />
                          )}
                        </TableCell>
                      );
                    }

                    if (feat.type === "numeric") {
                      return (
                        <TableCell key={p} align="center">
                          <Typography variant="body2">
                            {formatNumericEntitlement(val)}
                          </Typography>
                        </TableCell>
                      );
                    }

                    if (feat.type === "custom" && feat.format) {
                      return (
                        <TableCell key={p} align="center">
                          <Typography variant="body2">
                            {val != null ? feat.format(val) : "—"}
                          </Typography>
                        </TableCell>
                      );
                    }

                    return (
                      <TableCell key={p} align="center">
                        <Typography variant="caption">—</Typography>
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </React.Fragment>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

FeatureMatrix.propTypes = {
  plansData: PropTypes.object,
};

// ── Main Page ──────────────────────────────────────────────────────────────

export default function PricingPage() {
  const [isAnnual, setIsAnnual] = useState(false);
  const [addonDialog, setAddonDialog] = useState({
    open: false,
    plan: null,
    action: null,
  });
  const queryClient = useQueryClient();

  // Handle Stripe Checkout redirect (upgrade=success&session_id=...)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const upgrade = params.get("upgrade");
    const sessionId = params.get("session_id");

    if (upgrade === "success" && sessionId) {
      axios
        .put(endpoints.settings.v2.upgradeToPayg, { session_id: sessionId })
        .then(() => {
          enqueueSnackbar("Upgraded to Pay-as-you-go!", { variant: "success" });
          queryClient.invalidateQueries({ queryKey: ["v2-plans-and-addons"] });
        })
        .catch((err) => {
          enqueueSnackbar(
            err?.response?.data?.result || "Failed to confirm upgrade",
            { variant: "error" },
          );
        })
        .finally(() => {
          // Clean URL params
          window.history.replaceState({}, "", window.location.pathname);
        });
    } else if (upgrade === "cancelled") {
      enqueueSnackbar("Upgrade cancelled", { variant: "info" });
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const { data, isLoading } = useQuery({
    queryKey: ["v2-plans-and-addons"],
    queryFn: () => axios.get(endpoints.settings.v2.plansAndAddons),
    select: (res) => res.data?.result,
  });

  const closeDialog = () =>
    setAddonDialog({ open: false, plan: null, action: null });

  const upgradeMutation = useMutation({
    mutationFn: async () => {
      const res = await axios.post(endpoints.settings.v2.upgradeToPayg);
      const checkoutUrl = res.data?.result?.checkout_url;
      if (!checkoutUrl) throw new Error("Failed to create checkout session");
      // Redirect to Stripe Checkout — user enters card on Stripe's hosted page
      window.location.href = checkoutUrl;
    },
    onError: (err) =>
      enqueueSnackbar(err?.message || "Upgrade failed", { variant: "error" }),
  });

  const addonMutation = useMutation({
    mutationFn: (plan) => axios.post(endpoints.settings.v2.addAddon, { plan }),
    onSuccess: (res) => {
      enqueueSnackbar(`${res.data?.result?.plan} add-on activated!`, {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["v2-plans-and-addons"] });
      closeDialog();
    },
    onError: () => {
      enqueueSnackbar("Failed to add add-on", { variant: "error" });
      closeDialog();
    },
  });

  const removeMutation = useMutation({
    mutationFn: (plan) =>
      axios.post(endpoints.settings.v2.removeAddon, { plan }),
    onSuccess: () => {
      enqueueSnackbar("Add-on will be removed at end of billing period", {
        variant: "info",
      });
      queryClient.invalidateQueries({ queryKey: ["v2-plans-and-addons"] });
      queryClient.invalidateQueries({ queryKey: ["v2-billing-overview"] });
      queryClient.invalidateQueries({ queryKey: ["v2-usage-overview"] });
      closeDialog();
    },
    onError: () => {
      enqueueSnackbar("Failed to remove add-on", { variant: "error" });
      closeDialog();
    },
  });

  const reinstateMutation = useMutation({
    mutationFn: () => axios.put(endpoints.settings.v2.reinstateAddon),
    onSuccess: () => {
      enqueueSnackbar("Add-on reinstated. Your plan stays active.", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["v2-plans-and-addons"] });
      queryClient.invalidateQueries({ queryKey: ["v2-billing-overview"] });
      queryClient.invalidateQueries({ queryKey: ["v2-usage-overview"] });
    },
    onError: (err) =>
      enqueueSnackbar(
        err?.response?.data?.result || "Failed to reinstate add-on",
        { variant: "error" },
      ),
  });

  const downgradeMutation = useMutation({
    mutationFn: () => axios.post(endpoints.settings.v2.downgradeToFree),
    onSuccess: () => {
      enqueueSnackbar("Downgraded to Free plan", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["v2-plans-and-addons"] });
    },
    onError: (err) =>
      enqueueSnackbar(err?.response?.data?.result || "Downgrade failed", {
        variant: "error",
      }),
  });

  const handleUpgrade = (plan) => {
    if (plan === "payg") {
      upgradeMutation.mutate();
    } else if (plan === "free") {
      if (currentPlan !== "free" && currentPlan !== "payg") {
        removeMutation.mutate(currentPlan);
      } else if (currentPlan === "payg") {
        downgradeMutation.mutate();
      }
    }
  };

  const handleAddAddon = useCallback((plan) => {
    setAddonDialog({ open: true, plan, action: "add" });
  }, []);

  const handleRemoveAddon = useCallback((plan) => {
    setAddonDialog({ open: true, plan, action: "remove" });
  }, []);

  const handleReinstateAddon = useCallback(() => {
    reinstateMutation.mutate();
  }, [reinstateMutation]);

  const handleDialogConfirm = useCallback(() => {
    const { plan, action } = addonDialog;
    if (action === "add") {
      addonMutation.mutate(plan);
    } else if (action === "remove") {
      removeMutation.mutate(plan);
    }
  }, [addonDialog.plan, addonDialog.action]); // eslint-disable-line react-hooks/exhaustive-deps

  if (isLoading) {
    return (
      <Box p={3}>
        <Skeleton variant="text" width={300} height={40} />
        <Stack direction="row" spacing={2} mt={3}>
          <Skeleton variant="rounded" height={250} sx={{ flex: 1 }} />
          <Skeleton variant="rounded" height={250} sx={{ flex: 1 }} />
        </Stack>
      </Box>
    );
  }

  const currentPlan = data?.current_plan || "free";
  const isCustomPricing = data?.is_custom_pricing || false;
  const tiers = data?.tiers || [];
  const addons = data?.addons || [];
  const currentAddon = addons.find((a) => a.key === currentPlan);
  const dialogAddon = addons.find((a) => a.key === addonDialog.plan);
  const isDialogLoading = addonMutation.isPending || removeMutation.isPending;
  const storageBillingCopy = getStorageBillingCopy(data?.pricing?.storage);

  // Build plan data map for feature matrix
  const plansData = {};
  [...tiers, ...addons].forEach((p) => {
    plansData[p.key] = p;
  });

  return (
    <Box>
      <Typography variant="h5" fontWeight={700} mb={0.5}>
        Plans & Pricing
      </Typography>
      <Typography variant="body2" color="text.secondary" mb={3}>
        Start free or pay as you go. Add Boost, Scale, or Enterprise for
        advanced features and higher limits.
      </Typography>

      {/* Custom pricing banner + details */}
      {isCustomPricing && (
        <>
          <Paper
            variant="outlined"
            sx={(theme) => ({
              p: 3,
              mb: 3,
              borderRadius: 2,
              border: "1px solid",
              ...(theme.palette.mode === "light"
                ? { bgcolor: "info.lighter", borderColor: "info.light" }
                : {
                    bgcolor: alpha(theme.palette.info.main, 0.08),
                    borderColor: alpha(theme.palette.info.main, 0.2),
                  }),
            })}
          >
            <Stack direction="row" alignItems="center" spacing={2}>
              <Iconify
                icon="mdi:diamond"
                width={28}
                sx={{ color: "info.main" }}
              />
              <Box>
                <Typography variant="subtitle1" fontWeight={700}>
                  Custom Pricing
                  {data?.custom_details?.platform_fee > 0 && (
                    <Chip
                      label={`${fCurrency(data.custom_details.per_charge_amount)}/${
                        { 1: "mo", 3: "qtr", 6: "half", 12: "yr" }[
                          data.custom_details.platform_fee_billing_cycle
                        ] || "mo"
                      }`}
                      size="small"
                      sx={{ ml: 1 }}
                    />
                  )}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  You&apos;re on a custom pricing plan. Please reach out to
                  FutureAGI support for any queries or changes.
                </Typography>
              </Box>
            </Stack>
          </Paper>

          {/* Custom plan features */}
          {data?.custom_details?.features && (
            <>
              <Typography variant="subtitle1" fontWeight={600} mb={2}>
                Your plan features
              </Typography>
              <Paper
                variant="outlined"
                sx={{ borderRadius: 2, mb: 3, overflow: "hidden" }}
              >
                <Table size="small">
                  <TableBody>
                    {Object.entries(data.custom_details.features)
                      .filter(
                        ([key]) =>
                          !SKIP_FEATURE_PREFIXES.some((p) => key.startsWith(p)),
                      )
                      .filter(
                        ([key, v]) =>
                          FEATURE_LABELS[key] &&
                          (typeof v === "boolean" ||
                            (typeof v === "number" && v !== 0)),
                      )
                      .map(([key, v]) => (
                        <TableRow key={key}>
                          <TableCell sx={{ py: 1 }}>
                            <Typography variant="body2">
                              {FEATURE_LABELS[key] || key}
                            </Typography>
                          </TableCell>
                          <TableCell align="right" sx={{ py: 1 }}>
                            {typeof v === "boolean" ? (
                              <Iconify
                                icon={v ? "mdi:check" : "mdi:close"}
                                width={18}
                                sx={{
                                  color: v ? "success.main" : "error.main",
                                }}
                              />
                            ) : (
                              <Typography variant="body2" fontWeight={600}>
                                {v === -1 ? "Unlimited" : v.toLocaleString()}
                              </Typography>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                  </TableBody>
                </Table>
              </Paper>
            </>
          )}

          {/* Custom pricing tiers */}
          {data?.custom_details?.pricing &&
            Object.keys(data.custom_details.pricing).length > 0 && (
              <>
                <Typography variant="subtitle1" fontWeight={600} mb={1}>
                  Your pricing tiers
                </Typography>
                <Typography variant="body2" color="text.secondary" mb={2}>
                  Custom rates negotiated for your organization.
                </Typography>
                <TableContainer
                  component={Paper}
                  variant="outlined"
                  sx={{ borderRadius: 2, mb: 3 }}
                >
                  <Table size="small">
                    <TableHead>
                      <TableRow sx={{ "& th": { py: 2 } }}>
                        <TableCell sx={{ fontWeight: 700 }}>
                          Dimension
                        </TableCell>
                        <TableCell
                          sx={{
                            fontWeight: 700,
                            borderLeft: "1px solid",
                            borderLeftColor: "divider",
                          }}
                        >
                          Tier Range
                        </TableCell>
                        <TableCell
                          sx={{
                            fontWeight: 700,
                            borderLeft: "1px solid",
                            borderLeftColor: "divider",
                          }}
                        >
                          Price per Unit
                        </TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {Object.entries(data.custom_details.pricing).flatMap(
                        ([dimKey, dim]) =>
                          dim.tiers.map((tier, idx) => (
                            <TableRow key={`${dimKey}-${idx}`}>
                              {idx === 0 && (
                                <TableCell
                                  rowSpan={dim.tiers.length}
                                  sx={{ fontWeight: 600, verticalAlign: "top" }}
                                >
                                  {dim.display_name}
                                </TableCell>
                              )}
                              <TableCell
                                sx={{
                                  borderLeft: "1px solid",
                                  borderLeftColor: "divider",
                                }}
                              >
                                {tier.start.toLocaleString()} -{" "}
                                {tier.end ? tier.end.toLocaleString() : "+"}{" "}
                                {getPricingDisplayUnit(dimKey, dim)}
                              </TableCell>
                              <TableCell
                                sx={{
                                  borderLeft: "1px solid",
                                  borderLeftColor: "divider",
                                }}
                              >
                                {fCurrency(tier.rate, true)} per{" "}
                                {getPricingRateUnit(dimKey, dim)}
                              </TableCell>
                            </TableRow>
                          )),
                      )}
                    </TableBody>
                  </Table>
                </TableContainer>
              </>
            )}
        </>
      )}

      {/* Current plan status */}
      {currentPlan !== "free" && !isCustomPricing && (
        <Paper
          variant="outlined"
          sx={(theme) => ({
            p: 2,
            mb: 3,
            borderRadius: 2,
            border: "1px solid",
            ...(theme.palette.mode === "light"
              ? { bgcolor: "success.lighter", borderColor: "success.light" }
              : {
                  bgcolor: alpha(theme.palette.success.main, 0.08),
                  borderColor: alpha(theme.palette.success.main, 0.2),
                }),
          })}
        >
          <Stack direction="row" alignItems="center" spacing={2}>
            <Iconify
              icon="mdi:check-circle"
              width={24}
              sx={{ color: "success.main" }}
            />
            <Box>
              <Typography variant="subtitle2" fontWeight={600}>
                Current: {currentAddon?.display_name || "Pay-as-you-go"}
                {currentAddon && (
                  <Chip
                    label={`${fCurrency(currentAddon.platform_fee_monthly)}/mo`}
                    size="small"
                    sx={{ ml: 1 }}
                  />
                )}
              </Typography>
              {data?.billing_interval && (
                <Typography variant="caption" color="text.secondary">
                  Billed {data.billing_interval}
                  {data?.billing_period_end &&
                    ` \u00b7 Renews ${data.billing_period_end}`}
                </Typography>
              )}
            </Box>
          </Stack>
        </Paper>
      )}

      {/* Tiers: Free / PAYG (hidden for custom pricing) */}
      {!isCustomPricing && (
        <>
          <Typography variant="subtitle1" fontWeight={600} mb={1.5}>
            Choose your tier
          </Typography>
          <Stack direction="row" spacing={2} mb={4}>
            {tiers.map((tier) => {
              // PAYG shows as current for any paid plan (payg, boost, scale, enterprise)
              const isActiveTier =
                tier.key === "free"
                  ? currentPlan === "free"
                  : currentPlan !== "free";
              return (
                <TierCard
                  key={tier.key}
                  tier={tier}
                  isCurrent={isActiveTier}
                  onUpgrade={handleUpgrade}
                />
              );
            })}
          </Stack>
        </>
      )}

      {/* Add-ons: Boost / Scale / Enterprise (hidden for custom pricing) */}
      {!isCustomPricing && (
        <Stack direction="column" mb={1.5}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            mb={2}
          >
            <Typography variant="subtitle1" fontWeight={600}>
              Add-ons
            </Typography>
            <FormControlLabel
              control={
                <Switch
                  checked={isAnnual}
                  onChange={(e) => setIsAnnual(e.target.checked)}
                />
              }
              label={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" fontWeight={500}>
                    Annual billing
                  </Typography>
                  <Chip
                    label="Save 20%"
                    size="small"
                    sx={{
                      bgcolor: "success.lighter",
                      color: "success.dark",
                      fontWeight: 600,
                      fontSize: "0.75rem",
                      borderRadius: 1,
                    }}
                  />
                </Stack>
              }
            />
          </Stack>
          <Stack direction="row" spacing={2} mb={4}>
            {addons.map((addon) => {
              const planOrder = {
                free: 0,
                payg: 1,
                boost: 2,
                scale: 3,
                enterprise: 4,
              };
              const currentRank = planOrder[currentPlan] || 0;
              const addonRank = planOrder[addon.key] || 0;
              return (
                <AddonCard
                  key={addon.key}
                  addon={addon}
                  isCurrent={currentPlan === addon.key}
                  isIncluded={addonRank < currentRank}
                  isAnnual={isAnnual}
                  currentPlan={currentPlan}
                  pendingCancel={
                    currentPlan === addon.key && !!data?.pending_cancel
                  }
                  cancelAt={data?.cancel_at}
                  onAdd={handleAddAddon}
                  onRemove={handleRemoveAddon}
                  onReinstate={handleReinstateAddon}
                />
              );
            })}
          </Stack>
        </Stack>
      )}

      {/* Feature comparison (hidden for custom pricing) */}
      {!isCustomPricing && (
        <>
          <Divider sx={{ my: 3 }} />
          <Typography variant="subtitle1" fontWeight={600} mb={2}>
            Feature comparison
          </Typography>
          <FeatureMatrix plansData={plansData} />

          {/* Usage-based pricing */}
          <Divider sx={{ my: 3 }} />
          <Typography variant="subtitle1" fontWeight={600} mb={1}>
            Usage-based pricing
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={2}>
            All plans include free usage allowances. You only pay for what
            exceeds the free tier.
          </Typography>
          {storageBillingCopy && (
            <Paper
              variant="outlined"
              sx={{
                p: 2,
                mb: 2,
                borderRadius: 1,
                bgcolor: (theme) => alpha(theme.palette.info.main, 0.04),
                borderColor: (theme) => alpha(theme.palette.info.main, 0.16),
              }}
            >
              <Stack spacing={0.5}>
                <Typography variant="body2" fontWeight={600}>
                  Storage overages are billed monthly.
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {storageBillingCopy.allowance}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {storageBillingCopy.example}
                </Typography>
              </Stack>
            </Paper>
          )}

          {data?.pricing && (
            <TableContainer
              component={Paper}
              variant="outlined"
              sx={{
                borderRadius: 2,
                "& td, & th": { borderBottomStyle: "solid" },
              }}
            >
              <Table size="small">
                <TableHead>
                  <TableRow sx={{ "& th": { py: 2 } }}>
                    <TableCell
                      sx={{ fontWeight: 700, width: 200, fontSize: "1rem" }}
                    >
                      Dimension
                    </TableCell>
                    <TableCell
                      sx={{
                        fontWeight: 700,
                        fontSize: "1rem",
                        borderLeft: "1px solid",
                        borderLeftColor: "divider",
                      }}
                    >
                      Tier Range
                    </TableCell>
                    <TableCell
                      sx={{
                        fontWeight: 700,
                        fontSize: "1rem",
                        borderLeft: "1px solid",
                        borderLeftColor: "divider",
                      }}
                    >
                      Price per Unit
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.entries(data.pricing).map(([dimKey, pricing]) => {
                    const isSingleTier = pricing.tiers.length === 1;
                    const displayUnit = getPricingDisplayUnit(dimKey, pricing);
                    const rateUnit = getPricingRateUnit(dimKey, pricing);
                    return pricing.tiers.map((tier, i) => (
                      <TableRow key={`${dimKey}-${i}`}>
                        {i === 0 ? (
                          <TableCell
                            rowSpan={pricing.tiers.length}
                            sx={{
                              fontWeight: 600,
                              verticalAlign: "top",
                              borderRight: "1px solid",
                              borderRightColor: "divider",
                            }}
                          >
                            {pricing.display_name}
                          </TableCell>
                        ) : null}
                        <TableCell
                          sx={{
                            borderLeft: "1px solid",
                            borderLeftColor: "divider",
                          }}
                        >
                          {isSingleTier
                            ? "Flat rate (non-tiered)"
                            : tier.price_per_unit === 0
                              ? `First ${formatCompact(tier.up_to || 0)} ${displayUnit} (free)`
                              : tier.up_to
                                ? `${formatCompact(pricing.tiers[i - 1]?.up_to || 0)} – ${formatCompact(tier.up_to)} ${displayUnit}`
                                : `${formatCompact(pricing.tiers[i - 1]?.up_to || 0)}+ ${displayUnit}`}
                        </TableCell>
                        <TableCell
                          sx={{
                            borderLeft: "1px solid",
                            borderLeftColor: "divider",
                          }}
                        >
                          <Stack
                            direction="row"
                            justifyContent="space-between"
                            alignItems="center"
                          >
                            {tier.price_per_unit === 0 ? (
                              <Chip
                                label="Free"
                                size="small"
                                color="success"
                                variant="outlined"
                              />
                            ) : (
                              <Typography variant="body2" fontWeight={500}>
                                {fCurrency(tier.price_per_unit, true)}
                              </Typography>
                            )}
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              per {rateUnit}
                            </Typography>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    ));
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </>
      )}

      {/* Add / Remove add-on confirmation dialog */}
      <CustomDialog
        open={addonDialog.open}
        onClose={closeDialog}
        title={
          addonDialog.action === "add"
            ? `Add ${dialogAddon?.display_name || ""} Add-on`
            : `Remove ${dialogAddon?.display_name || ""}?`
        }
        actionButton={
          addonDialog.action === "add"
            ? `Add for ${fCurrency(dialogAddon?.platform_fee_monthly || 0)}/mo`
            : "Remove"
        }
        color={addonDialog.action === "remove" ? "error" : "primary"}
        onClickAction={handleDialogConfirm}
        loading={isDialogLoading}
        preTitleIcon={
          ADDON_ICONS[addonDialog.plan] ||
          (addonDialog.action === "add"
            ? "mdi:plus-circle"
            : "mdi:alert-circle")
        }
      >
        <DialogContent sx={{ px: 0 }}>
          {addonDialog.action === "add" && (
            <Box
              sx={{
                borderRadius: 0.5,
                bgcolor: (theme) => alpha(theme.palette.info.main, 0.06),
                border: (theme) =>
                  `1px solid ${alpha(theme.palette.info.main, 0.12)}`,
                p: 1,
                my: 2,
              }}
            >
              <Typography
                typography="s2_1"
                color="text.secondary"
                fontWeight={"500"}
              >
                The balance amount will be adjusted in your next billing cycle
              </Typography>
            </Box>
          )}
          {addonDialog.action === "remove" && (
            <Box
              sx={{
                borderRadius: 0.5,
                bgcolor: (theme) => alpha(theme.palette.warning.main, 0.06),
                border: (theme) =>
                  `1px solid ${alpha(theme.palette.warning.main, 0.12)}`,
                p: 1,
                my: 2,
              }}
            >
              <Typography
                typography="s2_1"
                color="text.secondary"
                fontWeight={"500"}
              >
                Your add-on will remain active until the end of the current
                billing period
              </Typography>
            </Box>
          )}
          {dialogAddon && (
            <BooleanFeatureList
              features={dialogAddon.features}
              isRemove={addonDialog.action === "remove"}
            />
          )}
        </DialogContent>
      </CustomDialog>
    </Box>
  );
}
