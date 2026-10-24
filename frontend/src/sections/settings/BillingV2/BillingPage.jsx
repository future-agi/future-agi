/**
 * Billing Page V2 — Usage-based billing dashboard (Phase 7c)
 *
 * Replaces wallet-based UI with:
 * - Current period cost breakdown (platform fee + per-dimension costs)
 * - Invoice history with expandable line items
 * - Payment method management
 * - Billing details
 */

import PageHeader from "src/components/page-header";
import { useState } from "react";
import PropTypes from "prop-types";
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

import axios, { endpoints } from "src/utils/axios";
import { fCurrency } from "src/utils/format-number";
import BudgetManager from "./BudgetManager";
import PaymentMethods from "./PaymentMethods";

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
          Current Period
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
            Current Period
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
            {fCurrency(data.total || 0)}
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
                      {item.amount < 0 ? "-" : ""}
                      {fCurrency(Math.abs(item.amount))}
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
                    {fCurrency(data.total || 0)}
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

function InvoiceRow({ inv }) {
  const [expanded, setExpanded] = useState(false);
  const status = STATUS_CONFIG[inv.status] || STATUS_CONFIG.draft;

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
        <TableCell sx={{ width: 30, pr: 0 }}>
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
        <TableCell>
          <Typography variant="body2">
            {inv.period_start} — {inv.period_end}
          </Typography>
        </TableCell>
        <TableCell>
          <Typography variant="body2" color="text.primary">
            {inv.plan}
          </Typography>
        </TableCell>
        <TableCell sx={{ fontVariantNumeric: "tabular-nums" }} align="right">
          <Typography variant="body2" fontWeight={500}>
            {fCurrency(inv.total)}
          </Typography>
        </TableCell>
        <TableCell align="center">
          <Chip
            icon={<Iconify icon={status.icon} width={16} />}
            label={status.label}
            size="small"
            color={status.color}
            variant="outlined"
          />
        </TableCell>
        <TableCell sx={{ fontVariantNumeric: "tabular-nums" }} align="right">
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
        <TableCell
          colSpan={6}
          sx={{ py: 0, borderBottom: expanded ? undefined : 0 }}
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
                            {item.unit_price
                              ? fCurrency(item.unit_price, true)
                              : "—"}
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
                            {item.amount < 0 ? "-" : ""}
                            {fCurrency(Math.abs(item.amount))}
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
};

function InvoiceHistory() {
  const { data: invoiceData, isLoading } = useQuery({
    queryKey: ["v2-invoices"],
    queryFn: () => axios.get(endpoints.settings.v2.invoices),
    select: (res) => res.data?.result?.invoices || [],
  });

  if (isLoading) {
    return <Skeleton variant="rounded" height={200} />;
  }

  const invoices = invoiceData || [];

  if (invoices.length === 0) {
    return (
      <Section variant="panel" sx={{ p: 3, textAlign: "center" }}>
        <Iconify
          icon="solar:document-text-linear"
          width={40}
          sx={{ color: "text.disabled", mb: 1 }}
        />
        <Typography variant="body2" color="text.secondary">
          No invoices yet. Your first invoice will appear at the end of the
          billing period.
        </Typography>
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
              Actions
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {invoices.map((inv) => (
            <InvoiceRow key={inv.id} inv={inv} />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────

export default function BillingPageV2() {
  const { data: billingData, isLoading } = useQuery({
    queryKey: ["v2-billing-overview"],
    queryFn: () => axios.get(endpoints.settings.v2.billingOverview),
    select: (res) => res.data?.result,
  });

  const { data: notifications } = useQuery({
    queryKey: ["v2-notifications"],
    queryFn: () => axios.get(endpoints.settings.v2.notifications),
    select: (res) => res.data?.result?.banners || [],
  });

  const [dismissedBanners, setDismissedBanners] = useState(new Set());

  if (isLoading) {
    return (
      <Box>
        <Skeleton variant="text" width={200} height={36} />
        <Skeleton variant="rounded" height={200} sx={{ mt: 2 }} />
        <Skeleton variant="rounded" height={300} sx={{ mt: 3 }} />
      </Box>
    );
  }

  return (
    <Box>
      <PageHeader
        title="Billing"
        description="Manage your billing, view invoices, and update payment methods."
        sx={{ mb: 3 }}
      />

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
        Invoice History
      </Typography>
      <InvoiceHistory />

      {/* Payment methods */}
      <Box mt={4}>
        <PaymentMethods />
      </Box>
    </Box>
  );
}
