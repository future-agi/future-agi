/**
 * Billing Page V2 — the one central billing page.
 *
 * On cloud: the usage-based platform plan (current period, budgets, invoice
 * history, payment methods). Wherever the AI Gateway's managed-credit ledger
 * answers (cloud or self-hosted): the AI Gateway credits (balance, top-up,
 * auto-reload, ledger, statement). Anyone with neither goes to their profile.
 */

import EmptyState from "src/components/empty-state";
import PageHeader from "src/components/page-header";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Stack,
  Paper,
  Skeleton,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  IconButton,
  Collapse,
  Alert,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import LoadFailedState from "src/components/load-failed-state";

import axios, { endpoints } from "src/utils/axios";
import { formatExactMoney } from "src/sections/common/format/money";
import BudgetManager from "./BudgetManager";
import PaymentMethods from "./PaymentMethods";
import GatewayCreditsSection from "./gateway-credits/GatewayCreditsSection";
import { CREDITS_ANCHOR } from "./gateway-credits/links";
import { useCentralBilling } from "./useCentralBilling";

// Billing amounts always show cents ($128.40, never $128.4 or $50), with
// thousands grouped and every digit the API sends kept: a sub-cent rate
// stays exact.
const usd = (value) => formatExactMoney(value, { grouping: true });

// An invoice's plan code, named as Usage Summary names the plan (the
// backend's PlanChoices labels; an add-on sits on Pay-as-you-go).
const PLAN_NAMES = {
  free: "Free",
  payg: "Pay-as-you-go",
  boost: "Pay-as-you-go + Boost",
  scale: "Pay-as-you-go + Scale",
  enterprise: "Pay-as-you-go + Enterprise",
  custom: "Custom",
};
const planName = (plan) => PLAN_NAMES[plan] || plan;

// Status badge colors
const STATUS_CONFIG = {
  paid: { color: "success", icon: "solar:check-circle-bold", label: "Paid" },
  draft: {
    color: "default",
    icon: "solar:clock-circle-linear",
    label: "Draft",
  },
  finalized: {
    color: "info",
    icon: "solar:clock-circle-linear",
    label: "Pending",
  },
  failed: {
    color: "error",
    icon: "solar:danger-circle-linear",
    label: "Failed",
  },
  void: { color: "default", icon: "solar:danger-circle-linear", label: "Void" },
};

// ── Current Period Summary ─────────────────────────────────────────────────

function CurrentPeriodSummary({ data }) {
  if (!data || data.plan === "free") {
    return (
      <Section variant="panel" sx={{ p: 3 }}>
        <Typography variant="subtitle1" fontWeight={600} mb={1}>
          Current period
        </Typography>
        <Typography variant="body2" color="text.secondary">
          You&apos;re on the Free plan. No billing this period.
        </Typography>
        <Button
          variant="outlined"
          size="small"
          href="/dashboard/settings/pricing"
          sx={{ mt: 2 }}
        >
          Upgrade to Pay-as-you-go
        </Button>
      </Section>
    );
  }

  return (
    <Section variant="panel" sx={{ p: 3 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        mb={2}
      >
        <Box>
          <Typography variant="subtitle1" fontWeight={600}>
            Current period
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {data.period}
          </Typography>
        </Box>
        <Box textAlign="right">
          <Typography variant="caption" color="text.secondary">
            Estimated total
          </Typography>
          <Typography variant="h4" fontWeight={600}>
            {usd(data.total)}
          </Typography>
        </Box>
      </Stack>

      {/* Line items */}
      {data.line_items?.length > 0 && (
        <TableContainer>
          <Table size="small">
            <TableBody>
              {data.line_items.map((item, i) => (
                <TableRow
                  key={`${item.description}-${i}`}
                  sx={{ "&:last-child td": { borderBottom: 0 } }}
                >
                  <TableCell sx={{ pl: 0 }}>
                    <Typography variant="body2">{item.description}</Typography>
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{ fontVariantNumeric: "tabular-nums", pr: 0 }}
                  >
                    <Typography
                      variant="body2"
                      fontWeight={500}
                      color={item.amount < 0 ? "success.main" : "text.primary"}
                    >
                      {usd(item.amount)}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell sx={{ pl: 0, borderBottom: 0 }}>
                  <Typography variant="subtitle2" fontWeight={600}>
                    Total
                  </Typography>
                </TableCell>
                <TableCell
                  align="right"
                  sx={{
                    fontVariantNumeric: "tabular-nums",
                    pr: 0,
                    borderBottom: 0,
                  }}
                >
                  <Typography variant="subtitle2" fontWeight={600}>
                    {usd(data.total)}
                  </Typography>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Section>
  );
}

CurrentPeriodSummary.propTypes = {
  data: PropTypes.shape({
    plan: PropTypes.string,
    period: PropTypes.string,
    total: PropTypes.number,
    line_items: PropTypes.arrayOf(
      PropTypes.shape({
        description: PropTypes.string,
        amount: PropTypes.number,
      }),
    ),
  }),
};

// ── Invoice History ────────────────────────────────────────────────────────

function InvoiceRow({ inv, isLast }) {
  const [expanded, setExpanded] = useState(false);
  const status = STATUS_CONFIG[inv.status] || STATUS_CONFIG.draft;
  // The table's outline already draws its bottom edge, so the last invoice
  // clears its own cell line, as CurrentPeriodSummary clears its last row:
  // the main row's while collapsed, the detail cell's while open.
  const lastLine = isLast && !expanded ? { borderBottom: 0 } : undefined;

  const { data: detail, isFetching } = useQuery({
    queryKey: ["v2-invoice-detail", inv.id],
    queryFn: () => axios.get(endpoints.settings.v2.invoiceDetail(inv.id)),
    select: (res) => res.data?.result,
    enabled: expanded,
  });

  return (
    <>
      <TableRow
        hover
        sx={{ cursor: "pointer" }}
        onClick={() => setExpanded(!expanded)}
      >
        <TableCell sx={{ width: 30, pr: 0, ...lastLine }}>
          <IconButton size="small">
            <Iconify
              icon={
                expanded
                  ? "solar:alt-arrow-down-linear"
                  : "solar:alt-arrow-right-linear"
              }
              width={20}
            />
          </IconButton>
        </TableCell>
        <TableCell sx={lastLine}>
          <Typography variant="body2">
            {inv.period_start} — {inv.period_end}
          </Typography>
        </TableCell>
        <TableCell sx={lastLine}>
          <Typography variant="body2" color="text.primary">
            {planName(inv.plan)}
          </Typography>
        </TableCell>
        <TableCell
          sx={{ fontVariantNumeric: "tabular-nums", ...lastLine }}
          align="right"
        >
          <Typography variant="body2" fontWeight={500}>
            {usd(inv.total)}
          </Typography>
        </TableCell>
        <TableCell align="center" sx={lastLine}>
          <Chip
            icon={<Iconify icon={status.icon} width={16} />}
            label={status.label}
            size="small"
            color={status.color}
            variant="outlined"
          />
        </TableCell>
        <TableCell
          sx={{ fontVariantNumeric: "tabular-nums", ...lastLine }}
          align="right"
        >
          {inv.stripe_pdf_url && (
            <IconButton
              className="row-hover-action"
              size="small"
              href={inv.stripe_pdf_url}
              target="_blank"
              title="Download PDF"
              onClick={(e) => e.stopPropagation()}
            >
              <Iconify icon="solar:download-minimalistic-linear" width={20} />
            </IconButton>
          )}
        </TableCell>
      </TableRow>
      <TableRow>
        {/* height: auto drops the theme's row height, so a collapsed
            invoice leaves no empty band under its row. */}
        <TableCell
          colSpan={6}
          sx={{
            py: 0,
            height: "auto",
            borderBottom: expanded && !isLast ? undefined : 0,
          }}
        >
          <Collapse in={expanded} timeout="auto" unmountOnExit>
            <Box sx={{ py: 2, px: 1 }}>
              {isFetching ? (
                <Skeleton variant="rounded" height={60} />
              ) : detail?.line_items?.length > 0 ? (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Item</TableCell>
                      <TableCell
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                        align="right"
                      >
                        Qty
                      </TableCell>
                      <TableCell
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                        align="right"
                      >
                        Rate
                      </TableCell>
                      <TableCell
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                        align="right"
                      >
                        Amount
                      </TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {detail.line_items.map((item, i) => (
                      <TableRow
                        key={`${item.line_type}-${item.dimension || ""}-${i}`}
                      >
                        <TableCell>
                          <Typography variant="caption">
                            {item.description}
                          </Typography>
                        </TableCell>
                        <TableCell
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                          align="right"
                        >
                          <Typography variant="caption">
                            {item.quantity
                              ? `${Number(item.quantity).toLocaleString()} ${item.unit || ""}`
                              : "—"}
                          </Typography>
                        </TableCell>
                        <TableCell
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                          align="right"
                        >
                          <Typography variant="caption">
                            {item.unit_price ? usd(item.unit_price) : "—"}
                          </Typography>
                        </TableCell>
                        <TableCell
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                          align="right"
                        >
                          <Typography
                            variant="caption"
                            fontWeight={500}
                            color={
                              item.amount < 0 ? "success.main" : "text.primary"
                            }
                          >
                            {usd(item.amount)}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <Typography variant="caption" color="text.secondary">
                  No line item details available.
                </Typography>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

InvoiceRow.propTypes = {
  inv: PropTypes.shape({
    id: PropTypes.string,
    period_start: PropTypes.string,
    period_end: PropTypes.string,
    plan: PropTypes.string,
    total: PropTypes.number,
    status: PropTypes.string,
    stripe_pdf_url: PropTypes.string,
  }).isRequired,
  isLast: PropTypes.bool,
};

function InvoiceHistory() {
  const {
    data: invoiceData,
    isLoading,
    isError,
    refetch,
    isRefetching,
  } = useQuery({
    queryKey: ["v2-invoices"],
    queryFn: () => axios.get(endpoints.settings.v2.invoices),
    select: (res) => res.data?.result?.invoices || [],
  });

  if (isLoading) {
    return <Skeleton variant="rounded" height={200} />;
  }

  if (isError && !invoiceData) {
    return (
      <LoadFailedState
        title="Invoices couldn't be loaded"
        onRetry={refetch}
        retrying={isRefetching}
      />
    );
  }

  const invoices = invoiceData || [];
  // The last column holds a PDF download; it is titled only when some
  // invoice has one, so the title never sits over cells that are all empty.
  const anyPdf = invoices.some((inv) => inv.stripe_pdf_url);

  if (invoices.length === 0) {
    return (
      <Section variant="panel" sx={{ p: 0 }}>
        <EmptyState
          icon="solar:document-text-linear"
          title="No invoices"
          description="Issued invoices are listed here."
          sx={{ pt: 6, pb: 6 }}
        />
      </Section>
    );
  }

  return (
    <TableContainer
      component={Paper}
      variant="outlined"
      sx={{ borderRadius: 2 }}
    >
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: 30 }} />
            <TableCell>Period</TableCell>
            <TableCell>Plan</TableCell>
            <TableCell
              sx={{ fontVariantNumeric: "tabular-nums" }}
              align="right"
            >
              Amount
            </TableCell>
            <TableCell align="center">Status</TableCell>
            <TableCell
              sx={{ fontVariantNumeric: "tabular-nums" }}
              align="right"
            >
              {anyPdf ? "Actions" : null}
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {invoices.map((inv, i) => (
            <InvoiceRow
              key={inv.id}
              inv={inv}
              isLast={i === invoices.length - 1}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────

export default function BillingPageV2() {
  const {
    isCloud,
    creditsAvailable,
    creditsError,
    retryCredits,
    creditsRetrying,
    isLoading: accessLoading,
  } = useCentralBilling();

  const {
    data: billingData,
    isLoading: platformLoading,
    isError,
    refetch,
    isRefetching,
  } = useQuery({
    queryKey: ["v2-billing-overview"],
    queryFn: () => axios.get(endpoints.settings.v2.billingOverview),
    select: (res) => res.data?.result,
    enabled: isCloud,
  });

  const { data: notifications } = useQuery({
    queryKey: ["v2-notifications"],
    queryFn: () => axios.get(endpoints.settings.v2.notifications),
    select: (res) => res.data?.result?.banners || [],
    enabled: isCloud,
  });

  const [dismissedBanners, setDismissedBanners] = useState(new Set());
  const isLoading = accessLoading || (isCloud && platformLoading);

  if (!isLoading && !isCloud && !creditsAvailable && !creditsError) {
    return <Navigate to="/dashboard/settings/profile-settings" replace />;
  }

  if (isLoading) {
    return (
      <Box>
        <Skeleton variant="text" width={200} height={36} />
        <Skeleton variant="rounded" height={200} sx={{ mt: 2 }} />
        <Skeleton variant="rounded" height={300} sx={{ mt: 3 }} />
      </Box>
    );
  }

  const header = (
    <PageHeader
      title="Billing"
      description={
        isCloud
          ? "Manage your billing, view invoices, and update payment methods."
          : "Future AGI credits for the AI Gateway."
      }
      sx={{ mb: 3 }}
    />
  );

  // Keep the cached-enabled wallet mounted so its checkout-return polling and
  // delayed refresh survive summary errors. Its own isError gate hides stale
  // content; a separate sibling supplies the visible error and Retry.
  const creditsSection =
    creditsAvailable || creditsError ? (
      <>
        {creditsError && (
          <Box
            component="section"
            id={CREDITS_ANCHOR}
            aria-label="AI Gateway credits"
          >
            <LoadFailedState
              title="AI Gateway credits couldn't be loaded"
              detail="Your credit balance and history are not shown until the summary loads."
              onRetry={retryCredits}
              retrying={creditsRetrying}
            />
          </Box>
        )}
        {creditsAvailable && <GatewayCreditsSection />}
      </>
    ) : null;

  // The plan, period and totals all come from this request. When it fails,
  // say so: a "Free plan, no billing this period" drawn from nothing is not
  // a fact.
  if (isCloud && isError && !billingData) {
    return (
      <Box>
        {header}
        <LoadFailedState
          title="Billing couldn't be loaded"
          detail="Your plan, current period and invoices are not shown until it loads."
          onRetry={refetch}
          retrying={isRefetching}
        />
        {creditsSection && <Box mt={4}>{creditsSection}</Box>}
      </Box>
    );
  }

  return (
    <Box>
      {header}

      {!isCloud && creditsSection}

      {isCloud && (
        <PlatformBilling
          billingData={billingData}
          notifications={notifications}
          dismissedBanners={dismissedBanners}
          setDismissedBanners={setDismissedBanners}
          creditsSection={creditsSection}
        />
      )}
    </Box>
  );
}

// The cloud platform plan, then the AI Gateway credits.
function PlatformBilling({
  billingData,
  notifications,
  dismissedBanners,
  setDismissedBanners,
  creditsSection,
}) {
  return (
    <Box>
      {/* Alert banners from notifications API */}
      {notifications?.filter((b) => !dismissedBanners.has(b.id)).length > 0 && (
        <Stack spacing={1} mb={3}>
          {notifications
            .filter((b) => !dismissedBanners.has(b.id))
            .map((banner) => (
              <Alert
                key={banner.id}
                severity={banner.type === "error" ? "error" : "warning"}
                onClose={
                  banner.dismissible
                    ? () =>
                        setDismissedBanners(
                          (prev) => new Set([...prev, banner.id]),
                        )
                    : undefined
                }
                action={
                  banner.action?.url ? (
                    <Button
                      color="inherit"
                      size="small"
                      href={banner.action.url}
                    >
                      {banner.action.label || "View"}
                    </Button>
                  ) : undefined
                }
              >
                {banner.message}
              </Alert>
            ))}
        </Stack>
      )}

      {/* Pending add-on cancellation notice */}
      {billingData?.pending_cancel && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          You&apos;ve requested to cancel your add-on. Your plan will become
          inactive
          {billingData.cancel_at
            ? ` on ${new Date(billingData.cancel_at).toLocaleDateString()}`
            : " at the end of the current billing cycle"}
          .
        </Alert>
      )}

      {/* Current period cost breakdown */}
      <CurrentPeriodSummary data={billingData} />

      {/* Usage budgets */}
      <Box mt={4}>
        <BudgetManager />
      </Box>

      {/* Invoice history */}
      <Typography variant="subtitle1" fontWeight={600} mt={4} mb={2}>
        Invoice history
      </Typography>
      <InvoiceHistory />

      {/* Payment methods */}
      <Box mt={4}>
        <PaymentMethods />
      </Box>

      {creditsSection && <Box mt={4}>{creditsSection}</Box>}
    </Box>
  );
}

PlatformBilling.propTypes = {
  billingData: PropTypes.object,
  notifications: PropTypes.array,
  dismissedBanners: PropTypes.instanceOf(Set).isRequired,
  setDismissedBanners: PropTypes.func.isRequired,
  creditsSection: PropTypes.node,
};
