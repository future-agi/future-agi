import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { useGatewayCreditSummary } from "./api";
import AutoReloadForm from "./AutoReloadForm";
import CreditLedger from "./CreditLedger";
import CreditStatement from "./CreditStatement";
import TopUpDialog from "./TopUpDialog";
import { CREDITS_ANCHOR } from "./links";
import { balanceTooltip, formatAmount, formatBalance } from "./format";
import { useCheckoutReturn } from "./useCheckoutReturn";

const GATEWAY_WARNINGS = {
  syncing:
    "The gateway is receiving this balance now; its live figures appear in a moment.",
  unreachable:
    "The gateway's live balance could not be read. The balance shown is the ledger's, which is the record.",
  managed_models_disabled:
    "The gateway serves no Future AGI models right now, so no credit is being spent.",
};

function Figure({ label, value, caption, title, large = false }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
      <Typography
        typography={large ? "m1" : "m3"}
        fontWeight={large ? "fontWeightMedium" : "fontWeightRegular"}
        color="text.primary"
        noWrap
        title={title || undefined}
      >
        {value}
      </Typography>
      {caption && (
        <Typography typography="s2" color="text.secondary">
          {caption}
        </Typography>
      )}
    </Box>
  );
}

Figure.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  caption: PropTypes.node,
  title: PropTypes.string,
  large: PropTypes.bool,
};

// AI Gateway credits, inside the central Settings > Billing page: the
// managed-credit balance from the ledger (the record) and the gateway, a
// top-up through the existing checkout where this build can sell credit, the
// low-balance and auto-reload settings, the ledger and the monthly statement.
export default function GatewayCreditsSection() {
  const titleId = useId();
  const [topUpOpen, setTopUpOpen] = useState(false);
  const { data: summary, isError } = useGatewayCreditSummary();
  useCheckoutReturn();

  if (isError || summary?.enabled !== true) return null;

  const canBuy = summary.billing_available === true;
  const pending = Number(summary.pending_settlement_usd || 0);
  // A build that sells no credit states no minimum top-up (nothing
  // gimmicky), and its credit was only granted.
  const fourth = pending > 0 || canBuy;

  return (
    <Paper
      variant="outlined"
      component="section"
      id={CREDITS_ANCHOR}
      aria-labelledby={titleId}
      sx={{
        p: { xs: 2, sm: 3 },
        borderRadius: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2.5,
        scrollMarginTop: 16,
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
        gap={1.5}
      >
        <Box>
          <Typography
            id={titleId}
            component="h2"
            variant="subtitle1"
            fontWeight={600}
          >
            AI Gateway credits
          </Typography>
          <Typography typography="s1" color="text.secondary">
            Prepaid Future AGI credits for credit-eligible catalog models
            through the AI Gateway.
          </Typography>
        </Box>
        {canBuy && (
          <Button
            variant="contained"
            color="primary"
            onClick={() => setTopUpOpen(true)}
          >
            Add credits
          </Button>
        )}
      </Stack>

      {summary.gateway_warning && GATEWAY_WARNINGS[summary.gateway_warning] && (
        <Alert
          severity={
            summary.gateway_warning === "unreachable" ? "warning" : "info"
          }
        >
          {GATEWAY_WARNINGS[summary.gateway_warning]}
        </Alert>
      )}
      {!canBuy && (
        <Alert severity="info">
          Credit purchase is unavailable in this deployment. Your balance,
          history and statements remain available.
        </Alert>
      )}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "1fr 1fr",
            md: `repeat(${fourth ? 4 : 3}, minmax(0, 1fr))`,
          },
          gap: 2,
        }}
      >
        <Box sx={{ gridColumn: { xs: "1 / -1", md: "auto" } }}>
          <Figure
            large
            label="Available"
            value={formatBalance(summary.available_usd)}
            title={balanceTooltip(summary.available_usd)}
            caption={
              <>
                {summary.gateway && (
                  <Box component="span" display="block">
                    {formatBalance(summary.gateway.available_usd)} available
                    now, after {formatAmount(summary.gateway.held_usd)} held by
                    requests in flight
                  </Box>
                )}
                {summary.markup_percent !== null &&
                  summary.markup_percent !== undefined && (
                    <Box component="span" display="block">
                      Future AGI credits are billed at provider cost plus a{" "}
                      {summary.markup_percent}% markup
                    </Box>
                  )}
              </>
            }
          />
        </Box>
        <Figure
          label={canBuy ? "Granted and bought" : "Granted"}
          value={formatAmount(summary.granted_usd)}
        />
        <Figure
          label="Spent"
          value={formatAmount(summary.settled_spend_usd)}
          title={
            summary.settled_spend_usd == null
              ? undefined
              : `${summary.settled_spend_usd} USD`
          }
        />
        {pending > 0 && (
          <Figure
            label="Spent, settling"
            value={formatAmount(pending)}
            caption="Charged at the gateway, not yet in the ledger"
          />
        )}
        {pending <= 0 && canBuy && (
          <Figure
            label="Minimum top-up"
            value={formatAmount(summary.min_topup_usd)}
          />
        )}
      </Box>
      <Divider />
      <AutoReloadForm summary={summary} canBuy={canBuy} />
      <Divider />
      <CreditLedger />
      <Divider />
      <CreditStatement />
      {canBuy && (
        <TopUpDialog
          open={topUpOpen}
          onClose={() => setTopUpOpen(false)}
          minTopup={Number(summary.min_topup_usd)}
        />
      )}
    </Paper>
  );
}
