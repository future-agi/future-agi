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

import PageHeader from "src/components/page-header";
import React, { useState, useCallback, useEffect, useId } from "react";
import PropTypes from "prop-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  usePlansAndAddons,
  PLANS_QUERY_KEY,
} from "src/hooks/use-plans-and-addons";
import {
  Box,
  Typography,
  Stack,
  Paper,
  Alert,
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
import Section from "src/components/section";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import Iconify from "src/components/iconify";
import LoadFailedState from "src/components/load-failed-state";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";
import ConfirmDowngrade from "src/pages/dashboard/settings/ConfirmDowngrade";

import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { formatExactMoney } from "src/sections/common/format/money";
import { canonicalEntries } from "src/utils/utils";
import { isNotBuiltPlanFeature } from "src/utils/planFormatters";
import {
  FEATURE_LABELS,
  ADDON_ICONS,
  ADDON_BASE_PLAN,
  getAddonBullets,
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

// Plans quotes every price with cents ($250.00, $1.50 per GB), as the credits
// card does; the exact digits are kept (a sub-cent rate stays as sent). The
// shared fCurrency keeps its own minimum for its other screens.
function fPlanPrice(value) {
  return formatExactMoney(value, {
    grouping: true,
    minimumFractionDigits: 2,
    missing: value == null ? "" : "—",
  });
}

const PAGE_TITLE = "Plans & pricing";
const PAGE_DESCRIPTION =
  "Start free or pay as you go. Add Boost, Scale, or Enterprise for advanced features and higher limits.";

// The plans this page has a card for. A stored plan outside them (an unknown
// or older code, or Custom without custom pricing) is named as it is, and is
// changed through support rather than from this page.
const LISTED_PLANS = ["free", "payg", "boost", "scale", "enterprise"];
// Names of stored plans this page has no card for, as the backend names them.
const UNLISTED_PLAN_NAMES = { custom: "Custom" };
const PLAN_NOT_LISTED =
  "This plan isn't listed here. Contact support to change it.";

// Constants imported from ./constants.js

// ── Tier Card (Free / PAYG) ────────────────────────────────────────────────

function TierCard({
  tier,
  isCurrent,
  onUpgrade,
  requiresAddonRemoval,
  pendingCancel,
  notListedId,
}) {
  const isFree = tier.key === "free";

  return (
    <Section
      variant="panel"
      sx={{
        p: 3,
        flex: 1,
        position: "relative",
        borderColor: isCurrent ? "text.primary" : "divider",
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

      <Typography variant="h6" fontWeight={600} mb={0.5}>
        {tier.display_name}
      </Typography>

      <Typography variant="body2" color="text.secondary" mb={2}>
        {isFree
          ? "Get started with core observability, evaluations, and dashboards. Usage capped at free tier limits."
          : "Everything in Free, with no usage caps. Pay only for what you use beyond the free allowance."}
      </Typography>

      {/* For a stored plan the page has no card for, moving to this tier
          would replace that plan, so the button is off and says why. */}
      {!isCurrent && (
        <Button
          variant={isFree ? "outlined" : "contained"}
          fullWidth
          onClick={() => onUpgrade(tier.key)}
          disabled={pendingCancel || Boolean(notListedId)}
          aria-describedby={notListedId}
        >
          {isFree
            ? requiresAddonRemoval
              ? pendingCancel
                ? "Add-on removal pending"
                : "Remove add-on first"
              : "Switch to Free"
            : "Upgrade to PAYG"}
        </Button>
      )}
      {isCurrent && (
        <Button variant="outlined" fullWidth disabled>
          Current plan
        </Button>
      )}
    </Section>
  );
}

TierCard.propTypes = {
  tier: PropTypes.shape({
    key: PropTypes.string,
    display_name: PropTypes.string,
  }).isRequired,
  isCurrent: PropTypes.bool,
  onUpgrade: PropTypes.func.isRequired,
  requiresAddonRemoval: PropTypes.bool,
  pendingCancel: PropTypes.bool,
  notListedId: PropTypes.string,
};

// ── Add-on Card (Boost / Scale / Enterprise) ───────────────────────────────

const ADDON_ELIGIBLE_PLANS = ["payg", "boost", "scale", "enterprise"];

function AddonCard({
  addon,
  baseFeatures,
  isCurrent,
  isIncluded,
  currentPlan,
  pendingCancel,
  cancelAt,
  onAdd,
  onRemove,
  onReinstate,
  notListedId,
}) {
  const iconName = ADDON_ICONS[addon.key] || "solar:star-bold";
  const monthlyFee = addon.platform_fee_monthly;
  const bullets = getAddonBullets(addon.key, addon.features, baseFeatures);
  const canPurchase = ADDON_ELIGIBLE_PLANS.includes(currentPlan);
  // Buying an add-on would replace a stored plan the page has no card for
  // (Custom without custom pricing included), so the button says why it is
  // off with the page's line.
  const disabledReason = notListedId
    ? PLAN_NOT_LISTED
    : "Upgrade to Pay-as-you-go to enable add-ons.";

  return (
    <Section
      variant="panel"
      sx={{
        p: 2.5,
        flex: 1,
        minHeight: 280,
        position: "relative",
        display: "flex",
        flexDirection: "column",
        borderColor: isCurrent ? "text.primary" : "divider",
        // Section wraps the children in a body box (its last child). Make it
        // a full-height column so the list grows and the button row's
        // mt: "auto" puts every Add button on the card's bottom edge.
        "& > :last-child": {
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
        },
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
          <Iconify icon={iconName} width={20} sx={{ color: "primary.main" }} />
        </Box>
        <Box>
          <Typography variant="subtitle1" fontWeight={600}>
            {addon.display_name}
          </Typography>
          <Stack direction="row" alignItems="baseline" spacing={0.5}>
            <Typography variant="h5" fontWeight={600}>
              {fPlanPrice(monthlyFee)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              /mo
            </Typography>
          </Stack>
        </Box>
      </Stack>

      {bullets.length > 0 && (
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
          {bullets.map((item) => (
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
                variant="outlined"
                color="primary"
                size="small"
                fullWidth
                disabled
                aria-describedby={notListedId}
              >
                Add {addon.display_name}
              </Button>
            </span>
          </Tooltip>
        ) : (
          <Button
            variant="outlined"
            color="primary"
            size="small"
            fullWidth
            onClick={() => onAdd(addon.key)}
          >
            Add {addon.display_name}
          </Button>
        )}
      </Box>
    </Section>
  );
}

AddonCard.propTypes = {
  addon: PropTypes.shape({
    key: PropTypes.string,
    display_name: PropTypes.string,
    platform_fee_monthly: PropTypes.number,
    features: PropTypes.object,
  }).isRequired,
  baseFeatures: PropTypes.object,
  isCurrent: PropTypes.bool,
  isIncluded: PropTypes.bool,
  currentPlan: PropTypes.string,
  pendingCancel: PropTypes.bool,
  cancelAt: PropTypes.string,
  onAdd: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onReinstate: PropTypes.func.isRequired,
  notListedId: PropTypes.string,
};

// ── Boolean Feature List (shared by add/remove dialogs) ───────────────────

// FEATURE_LABELS imported from ./constants

function BooleanFeatureList({ features, isRemove }) {
  // Show boolean features that are true + numeric features that are > 0
  const seenLabels = new Set();
  const displayFeatures = canonicalEntries(features)
    .filter(([key, v]) => {
      if (SKIP_FEATURE_PREFIXES.some((p) => key.startsWith(p))) return false;
      if (isNotBuiltPlanFeature(key)) return false;
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
    }))
    .filter(({ label }) => {
      if (seenLabels.has(label)) return false;
      seenLabels.add(label);
      return true;
    });

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
              icon={isRemove ? "solar:close-linear" : "solar:check-linear"}
              width={16}
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
          <TableRow>
            <TableCell sx={{ minWidth: 200 }}>Feature</TableCell>
            {plans.map((p) => (
              <TableCell key={p} align="center" sx={{ minWidth: 100 }}>
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
                    fontWeight: 600,
                    fontSize: "0.75rem",
                    textTransform: "none",
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
                            icon="solar:check-linear"
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
                              icon="solar:check-linear"
                              width={20}
                              sx={{ color: "success.main" }}
                            />
                          ) : (
                            <Iconify
                              icon="solar:close-linear"
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
  const [addonDialog, setAddonDialog] = useState({
    open: false,
    plan: null,
    action: null,
  });
  const [downgradeOpen, setDowngradeOpen] = useState(false);
  const queryClient = useQueryClient();
  const notListedId = useId();

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
          queryClient.invalidateQueries({ queryKey: PLANS_QUERY_KEY });
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

  // The page shows a failed read itself (Plans couldn't be loaded), so the
  // app adds no toast on top of it.
  const { data, isLoading, isError, refetch, isRefetching } = usePlansAndAddons(
    true,
    { errorHandled: true },
  );

  // Close only: the plan and action stay until the next open, so the dialog
  // keeps its own title and button while it fades out (never "Remove ?").
  const closeDialog = () => setAddonDialog((d) => ({ ...d, open: false }));

  const upgradeMutation = useMutation({
    mutationFn: async () => {
      const res = await axios.post(endpoints.settings.v2.upgradeToPayg, {});
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
      queryClient.invalidateQueries({ queryKey: PLANS_QUERY_KEY });
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
      queryClient.invalidateQueries({ queryKey: PLANS_QUERY_KEY });
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
      queryClient.invalidateQueries({ queryKey: PLANS_QUERY_KEY });
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
    mutationFn: () => axios.post(endpoints.settings.v2.downgradeToFree, {}),
    onSuccess: () => {
      enqueueSnackbar("Downgraded to Free plan", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: PLANS_QUERY_KEY });
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
      if (currentPlan === "payg") {
        setDowngradeOpen(true);
      } else if (hasAddon && !data?.pending_cancel) {
        setAddonDialog({ open: true, plan: currentPlan, action: "remove" });
      }
    }
  };

  const handleDowngradeConfirm = () => {
    if (currentPlan === "payg") {
      downgradeMutation.mutate();
    }
    setDowngradeOpen(false);
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

  // Every view opens with the same title, so it doesn't pop in when the plans
  // arrive.
  const header = (
    <PageHeader
      title={PAGE_TITLE}
      description={PAGE_DESCRIPTION}
      sx={{ mb: 3 }}
    />
  );

  // The loading and failed views sit on the loaded page's edges (no padding of
  // their own), so nothing moves when the plans arrive.
  if (isLoading) {
    return (
      <Box>
        {header}
        <Stack direction="row" spacing={2}>
          <Skeleton variant="rounded" height={250} sx={{ flex: 1 }} />
          <Skeleton variant="rounded" height={250} sx={{ flex: 1 }} />
        </Stack>
      </Box>
    );
  }

  // Never draw an empty tier picker or a feature table of dashes from a
  // failed request.
  if (isError && !data) {
    return (
      <Box>
        {header}
        <LoadFailedState
          title="Plans couldn't be loaded"
          detail="Your current plan and the plans you can move to are not shown until they load."
          onRetry={refetch}
          retrying={isRefetching}
        />
      </Box>
    );
  }

  const currentPlan = data?.current_plan || "free";
  const hasAddon = ["boost", "scale", "enterprise"].includes(currentPlan);
  const isCustomPricing =
    data?.isCustomPricing ?? data?.is_custom_pricing ?? false;
  const planChangeLocked =
    data?.planChangeLocked ?? data?.plan_change_locked ?? false;
  const planChangeLockedReason =
    data?.planChangeLockedReason ?? data?.plan_change_locked_reason ?? "";
  const customDetails = data?.customDetails ?? data?.custom_details ?? null;
  const tiers = data?.tiers || [];
  const addons = data?.addons || [];
  const currentAddon = addons.find((a) => a.key === currentPlan);
  const dialogAddon = addons.find((a) => a.key === addonDialog.plan);
  const isDialogLoading = addonMutation.isPending || removeMutation.isPending;

  // Build plan data map for feature matrix
  const plansData = {};
  [...tiers, ...addons].forEach((p) => {
    plansData[p.key] = p;
  });

  // A stored plan this page has no card for is named as it is (its known
  // name, else its code), never as another plan, and no tier is marked
  // Current for it.
  const planNotListed = !isCustomPricing && !LISTED_PLANS.includes(currentPlan);
  const currentPlanName = planNotListed
    ? plansData[currentPlan]?.display_name ||
      UNLISTED_PLAN_NAMES[currentPlan] ||
      currentPlan
    : currentAddon?.display_name || "Pay-as-you-go";
  const notListedDescription = planNotListed ? notListedId : undefined;

  return (
    <Box>
      {header}

      {planChangeLocked && (
        <Alert severity="info" sx={{ mb: 3 }}>
          {planChangeLockedReason}
        </Alert>
      )}

      {/* Custom pricing banner + details */}
      {isCustomPricing && (
        <>
          <Alert
            severity="info"
            icon={false}
            sx={{ mb: 3, "& .MuiAlert-message": { width: 1 } }}
          >
            <Stack direction="row" alignItems="center" spacing={2}>
              <Iconify
                icon="mdi:diamond"
                width={28}
                sx={{ color: "info.main" }}
              />
              <Box>
                <Typography variant="subtitle1" fontWeight={600}>
                  Custom Pricing
                  {customDetails?.platform_fee > 0 && (
                    <Chip
                      label={`${fPlanPrice(customDetails.per_charge_amount)}/${
                        { 1: "mo", 3: "qtr", 6: "half", 12: "yr" }[
                          customDetails.platform_fee_billing_cycle
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
          </Alert>

          {/* Custom plan features */}
          {customDetails?.features && (
            <>
              <Typography variant="subtitle1" fontWeight={600} mb={2}>
                Your plan features
              </Typography>
              <Section variant="panel" sx={{ p: 0, mb: 3, overflow: "hidden" }}>
                <Table size="small">
                  <TableBody>
                    {canonicalEntries(customDetails.features)
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
                          <TableCell
                            align="right"
                            sx={{ fontVariantNumeric: "tabular-nums", py: 1 }}
                          >
                            {typeof v === "boolean" ? (
                              <Iconify
                                icon={
                                  v
                                    ? "solar:check-linear"
                                    : "solar:close-linear"
                                }
                                width={20}
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
              </Section>
            </>
          )}

          {/* Custom pricing tiers */}
          {customDetails?.pricing &&
            Object.keys(customDetails.pricing).length > 0 && (
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
                      <TableRow>
                        <TableCell>Dimension</TableCell>
                        <TableCell
                          sx={{
                            borderLeft: "1px solid",
                            borderLeftColor: "divider",
                          }}
                        >
                          Tier Range
                        </TableCell>
                        <TableCell
                          sx={{
                            borderLeft: "1px solid",
                            borderLeftColor: "divider",
                          }}
                        >
                          Price per Unit
                        </TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {canonicalEntries(customDetails.pricing).flatMap(
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
                                  fontVariantNumeric: "tabular-nums",
                                  borderLeft: "1px solid",
                                  borderLeftColor: "divider",
                                }}
                              >
                                {tier.start.toLocaleString()} -{" "}
                                {tier.end ? tier.end.toLocaleString() : "+"}{" "}
                                {dim.display_unit}
                              </TableCell>
                              <TableCell
                                sx={{
                                  borderLeft: "1px solid",
                                  borderLeftColor: "divider",
                                }}
                              >
                                {fPlanPrice(tier.rate)} per {dim.display_unit}
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
      {currentPlan !== "free" && !isCustomPricing && !planChangeLocked && (
        <Alert
          severity="success"
          icon={false}
          sx={{ mb: 3, "& .MuiAlert-message": { width: 1 } }}
        >
          <Stack direction="row" alignItems="center" spacing={2}>
            <Iconify
              icon="solar:check-circle-bold"
              width={24}
              sx={{ color: "success.main" }}
            />
            <Box>
              <Typography variant="subtitle2" fontWeight={600}>
                Current: {currentPlanName}
                {currentAddon && (
                  <Chip
                    label={`${fPlanPrice(currentAddon.platform_fee_monthly)}/mo`}
                    size="small"
                    sx={{ ml: 1 }}
                  />
                )}
              </Typography>
              {planNotListed && (
                <Typography
                  id={notListedId}
                  variant="body2"
                  color="text.secondary"
                >
                  {PLAN_NOT_LISTED}
                </Typography>
              )}
              {data?.billing_interval && (
                <Typography variant="caption" color="text.secondary">
                  Billed {data.billing_interval}
                  {data?.billing_period_end &&
                    ` \u00b7 Renews ${data.billing_period_end}`}
                </Typography>
              )}
            </Box>
          </Stack>
        </Alert>
      )}

      {/* Tiers: Free / PAYG (hidden for custom pricing) */}
      {!isCustomPricing && !planChangeLocked && (
        <>
          <Typography variant="subtitle1" fontWeight={600} mb={1.5}>
            Choose your tier
          </Typography>
          {hasAddon && (
            <Alert severity="info" sx={{ mb: 2 }}>
              To switch to Free, first remove your add-on.{" "}
              {data?.pending_cancel
                ? data?.cancel_at
                  ? `Removal is scheduled for ${new Date(data.cancel_at).toLocaleDateString()}. `
                  : "Removal is scheduled for the end of your billing period. "
                : "Your add-on stays active until the end of your billing period. "}
              Once removal takes effect, return here and choose Switch to Free.
              Removing an add-on does not switch you to Free; usage remains on
              Pay-as-you-go until you confirm that change.
            </Alert>
          )}
          <Stack direction="row" spacing={2} mb={4}>
            {tiers.map((tier) => {
              // PAYG shows as current for any paid plan (payg, boost, scale,
              // enterprise); no tier is current for an unlisted plan.
              const isActiveTier =
                tier.key === "free"
                  ? currentPlan === "free"
                  : currentPlan !== "free" && !planNotListed;
              return (
                <TierCard
                  key={tier.key}
                  tier={tier}
                  isCurrent={isActiveTier}
                  onUpgrade={handleUpgrade}
                  requiresAddonRemoval={tier.key === "free" && hasAddon}
                  pendingCancel={
                    tier.key === "free" && hasAddon && !!data?.pending_cancel
                  }
                  notListedId={notListedDescription}
                />
              );
            })}
          </Stack>
        </>
      )}

      {/* Add-ons: Boost / Scale / Enterprise (hidden for custom pricing) */}
      {!isCustomPricing && !planChangeLocked && (
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
            {/* Annual billing is not built in this release: the selector stays
                visible, off and disabled, so cards and purchase keep the
                monthly fee. */}
            <FormControlLabel
              disabled
              control={<Switch checked={false} disabled />}
              label={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" fontWeight={500}>
                    Annual billing
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {NOT_IN_RELEASE}
                  </Typography>
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
                  baseFeatures={plansData[ADDON_BASE_PLAN[addon.key]]?.features}
                  isCurrent={currentPlan === addon.key}
                  isIncluded={addonRank < currentRank}
                  currentPlan={currentPlan}
                  pendingCancel={
                    currentPlan === addon.key && !!data?.pending_cancel
                  }
                  cancelAt={data?.cancel_at}
                  onAdd={handleAddAddon}
                  onRemove={handleRemoveAddon}
                  onReinstate={handleReinstateAddon}
                  notListedId={notListedDescription}
                />
              );
            })}
          </Stack>
        </Stack>
      )}

      {/* Feature comparison (hidden for custom pricing) */}
      {!isCustomPricing && !planChangeLocked && (
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
                  <TableRow>
                    <TableCell sx={{ width: 200 }}>Dimension</TableCell>
                    <TableCell
                      sx={{
                        borderLeft: "1px solid",
                        borderLeftColor: "divider",
                      }}
                    >
                      Tier Range
                    </TableCell>
                    <TableCell
                      sx={{
                        borderLeft: "1px solid",
                        borderLeftColor: "divider",
                      }}
                    >
                      Price per Unit
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {canonicalEntries(data.pricing).map(([dimKey, pricing]) => {
                    const isSingleTier = pricing.tiers.length === 1;
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
                              ? `First ${formatCompact(tier.up_to || 0)} ${pricing.display_unit} (free)`
                              : tier.up_to
                                ? `${formatCompact(pricing.tiers[i - 1]?.up_to || 0)} – ${formatCompact(tier.up_to)} ${pricing.display_unit}`
                                : `${formatCompact(pricing.tiers[i - 1]?.up_to || 0)}+ ${pricing.display_unit}`}
                        </TableCell>
                        <TableCell
                          sx={{
                            borderLeft: "1px solid",
                            borderLeftColor: "divider",
                          }}
                        >
                          {/* The unit follows the price ("$2.00 per GB");
                              a free tier has no rate to qualify. */}
                          {tier.price_per_unit === 0 ? (
                            <Chip
                              label="Free"
                              size="small"
                              color="success"
                              variant="outlined"
                            />
                          ) : (
                            <Stack
                              direction="row"
                              alignItems="baseline"
                              spacing={0.5}
                            >
                              <Typography variant="body2" fontWeight={500}>
                                {fPlanPrice(tier.price_per_unit)}
                              </Typography>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                per {pricing.display_unit}
                              </Typography>
                            </Stack>
                          )}
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
            ? `Add for ${fPlanPrice(dialogAddon?.platform_fee_monthly) || "—"}/mo`
            : "Remove"
        }
        color={addonDialog.action === "remove" ? "error" : "primary"}
        onClickAction={handleDialogConfirm}
        loading={isDialogLoading}
        preTitleIcon={
          ADDON_ICONS[addonDialog.plan] ||
          (addonDialog.action === "add"
            ? "solar:add-circle-linear"
            : "solar:danger-circle-linear")
        }
      >
        <DialogContent sx={{ px: 0 }}>
          {addonDialog.action === "add" && (
            <Box
              sx={{
                borderRadius: "4px",
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
                borderRadius: "4px",
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
                billing period. This only removes the add-on; you will remain on
                Pay-as-you-go. To switch to Free, return here after removal
                takes effect and choose Switch to Free.
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

      <ConfirmDowngrade
        open={downgradeOpen}
        onClose={() => setDowngradeOpen(false)}
        onConfirm={handleDowngradeConfirm}
        isLoading={downgradeMutation.isPending}
      />
    </Box>
  );
}
