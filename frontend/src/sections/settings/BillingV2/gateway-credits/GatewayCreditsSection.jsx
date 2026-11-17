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
import {
  AUTO_RELOAD_NOT_RUNNING_TEXT,
  autoReloadUnavailable,
} from "./autoReloadConsent";
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

// At or below zero the gateway refuses Future AGI model calls (402
// managed_credits_exhausted). "Empty", not "used up": an org never credited
// is at zero too. The next step
// this deployment allows: Add credits where it sells credit, else a grant,
// which only the deployment's staff or gateway administrator can make.
const OUT_OF_CREDIT = {
  buy: "Your Future AGI credit balance is empty. The gateway refuses requests to Future AGI models until credit is added; select Add credits to top up. Requests through your own provider keys are not affected.",
  grant:
    "Your Future AGI credit balance is empty. The gateway refuses requests to Future AGI models until credit is added; ask the administrator of this deployment to add credit. Requests through your own provider keys are not affected.",
};

// Back from checkout, the landing read answers "pending" alike for an unpaid
// session and for a paid one not yet credited, so this says only that the
// payment is being confirmed, never whether it was taken.
const CONFIRMING =
  "Confirming your payment. Your balance updates here once the top-up is credited.";

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
  const { confirming } = useCheckoutReturn();

  if (isError || summary?.enabled !== true) return null;

  const canBuy = summary.billing_available === true;
  // The gateway serves no Future AGI models, so credit bought now could not
  // be spent. Not an unreachable gateway, which also reads as unavailable.
  const purchasePaused = summary.gateway_warning === "managed_models_disabled";
  const pending = Number(summary.pending_settlement_usd || 0);
  // A build that sells no credit states no minimum top-up (nothing
  // gimmicky), and its credit was only granted.
  const fourth = pending > 0 || canBuy;
  // The ledger's exact balance, not a missing one. While the gateway serves
  // no Future AGI models its warning already says no credit is spent.
  const available =
    summary.available_usd == null || summary.available_usd === ""
      ? NaN
      : Number(summary.available_usd);
  const outOfCredit =
    Number.isFinite(available) && available <= 0 && !purchasePaused;
  // Saved as on, but no automatic reload runs in this build: say so here
  // too, so the balance is never read as protected by a reload.
  const reloadNotRunning =
    summary.auto_reload?.enabled === true && autoReloadUnavailable(summary);

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
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ xs: "flex-start", sm: "center" }}
            gap={1.5}
          >
            {purchasePaused && (
              <Typography
                id={`${titleId}-paused`}
                typography="s2"
                color="text.secondary"
              >
                Credit purchase is paused while no Future AGI models are served.
              </Typography>
            )}
            <Button
              variant="contained"
              color="primary"
              disabled={purchasePaused}
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
              aria-describedby={
                purchasePaused ? `${titleId}-paused` : undefined
              }
              onClick={() => setTopUpOpen(true)}
            >
              Add credits
            </Button>
          </Stack>
        )}
      </Stack>

      {/* A build that sells no credit has no payment to confirm (its
          checkout answers 503); a stale success URL must not stack this
          above "Credit purchase is unavailable". */}
      {canBuy && confirming && (
        <Alert severity="info" role="status">
          {CONFIRMING}
        </Alert>
      )}
      {summary.gateway_warning && GATEWAY_WARNINGS[summary.gateway_warning] && (
        <Alert
          severity={
            summary.gateway_warning === "unreachable" ? "warning" : "info"
          }
        >
          {GATEWAY_WARNINGS[summary.gateway_warning]}
        </Alert>
      )}
      {outOfCredit && (
        <Alert severity="warning">
          {canBuy ? OUT_OF_CREDIT.buy : OUT_OF_CREDIT.grant}
        </Alert>
      )}
      {!canBuy && (
        <Alert severity="info">
          Credit purchase is unavailable in this deployment. Your balance,
          history and statements remain available.
        </Alert>
      )}
      {reloadNotRunning && (
        <Alert severity="info">{AUTO_RELOAD_NOT_RUNNING_TEXT}</Alert>
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
            // The gateway's figure is the ledger's less what is still
            // settling and what requests in flight hold. Naming the settling
            // amount (the Spent, settling figure, to the digit) makes the
            // caption add up to the headline. The first clause keeps its own
            // element: the e2e wallet walk (e2e/lib/walk/wallet-read.ts)
            // reads it exactly.
            caption={
              summary.gateway ? (
                <>
                  <span>
                    {formatBalance(summary.gateway.available_usd)} available
                    now, after {formatAmount(summary.gateway.held_usd)} held by
                    requests in flight
                  </span>
                  {pending > 0 && <>, and {formatAmount(pending)} settling</>}
                </>
              ) : undefined
            }
          />
        </Box>
        {/* granted_usd is the ledger's credit total: grants and top-ups, net
            of operator adjustments. */}
        <Figure
          label="Credited"
          value={formatAmount(summary.granted_usd)}
          caption="Grants and top-ups, after any adjustments"
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
          open={topUpOpen && !purchasePaused}
          onClose={() => setTopUpOpen(false)}
          minTopup={Number(summary.min_topup_usd)}
        />
      )}
    </Paper>
  );
}
