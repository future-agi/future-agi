import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  Collapse,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { GatewayIcon } from "../shell/gateway-icons";
import {
  EMPTY,
  balanceTooltip,
  formatBalance,
  formatCost,
  formatLatency,
  isUnpriced,
} from "../utils/formatters";
import { asList, asNumber, asText, parseAutoDecision } from "./autoDecision";
import {
  BILLING_MODES,
  COST_SOURCES,
  CREDENTIAL_TYPES,
  labelOf,
} from "./explorerLabels";
import { DetailRow, DetailSection } from "../components/frame/DetailRows";
import { AutoContextRows, PolicyRows } from "./RoutingContextRows";
import { CHARGED_AT_HOLD, callEndOf } from "./callEnd";
import { noChargeComputed } from "./logCost";
import { GATEWAY_MONO } from "../utils/fonts";
import { providerLabel } from "../utils/providerLabel";

// Billing and auto-routing blocks for the request drawer's Overview tab. Each
// renders nothing when the log carries none of its metadata.

/** Whether the auto block renders, and so carries the policy rows. */
export const isAutoRouted = (log) => Boolean(asText(log.metadata?.auto_model));

// ---------------------------------------------------------------------------
// Billing
// ---------------------------------------------------------------------------

export function BillingSection({ log }) {
  const meta = log.metadata || {};
  // Log entry v2's billing block (D1) backs up the metadata keys.
  const billing = log.billing || {};
  const mode = asText(meta.billing_mode) || asText(billing.mode);
  if (!mode) return null;

  const managed = mode === "managed";
  // One amount: what the org pays on this route (credits: the charge the
  // ledger debits; own key: the provider's price). The provider cost and
  // margin inside a credits charge are never shown apart.
  const billed = asNumber(log.cost);
  const source = asText(meta.cost_source) || asText(billing.cost_source);
  const remaining = asNumber(meta.managed_credits_remaining);
  const credential = asText(billing.credential_type);
  // A call sent and ended without usage says why it was charged its hold.
  const chargedAtHold = Boolean(callEndOf(log)?.chargedAtHold);

  return (
    <DetailSection title="Billing">
      <Box>
        <DetailRow label="Mode">{BILLING_MODES[mode] || mode}</DetailRow>
        {credential && (
          <DetailRow label="Credential">
            {labelOf(CREDENTIAL_TYPES, credential)}
          </DetailRow>
        )}
        <DetailRow label={managed ? "Billed" : "Cost"}>
          {billed != null && !isUnpriced(log) && !noChargeComputed(log) ? (
            <Box component="span" title={`${billed} USD`}>
              {formatCost(billed)}
            </Box>
          ) : (
            EMPTY
          )}
          {!managed && " (using your own provider key)"}
        </DetailRow>
        {source && (
          <DetailRow label="Price source">
            {labelOf(COST_SOURCES, source)}
          </DetailRow>
        )}
        {managed && remaining != null && (
          <DetailRow label="Credit remaining">
            {/* Billing's balance rule: cut to the cent, exact on hover. */}
            <Box
              component="span"
              title={balanceTooltip(remaining) ?? undefined}
            >
              {formatBalance(remaining)}
            </Box>
          </DetailRow>
        )}
      </Box>
      {chargedAtHold ? (
        <Typography typography="s2" color="text.secondary">
          {CHARGED_AT_HOLD}
        </Typography>
      ) : (
        billing.usage_estimated === true && (
          <Typography typography="s2" color="text.secondary">
            Usage was estimated: the provider reported no token counts.
          </Typography>
        )
      )}
      {managed && source === "unpriced" && (
        <Alert severity="warning">
          No price was found for this model, so this request was not billed.
        </Alert>
      )}
    </DetailSection>
  );
}

BillingSection.propTypes = { log: PropTypes.object.isRequired };

// ---------------------------------------------------------------------------
// Auto routing
// ---------------------------------------------------------------------------

const fixed = (value, digits = 2) => {
  const n = asNumber(value);
  return n == null ? "—" : n.toFixed(digits);
};

function RankedTable({ ranked, chosen }) {
  return (
    <TableContainer
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, mt: 1 }}
    >
      <Table size="small" aria-label="Auto routing ranking">
        <TableHead>
          <TableRow>
            <TableCell sx={{ fontWeight: 600 }}>Model</TableCell>
            <TableCell sx={{ fontWeight: 600 }} align="right">
              Tier
            </TableCell>
            <TableCell sx={{ fontWeight: 600 }} align="right">
              Score
            </TableCell>
            <TableCell sx={{ fontWeight: 600 }} align="right">
              Est. cost
            </TableCell>
            <TableCell sx={{ fontWeight: 600 }} align="right">
              Latency
            </TableCell>
            <TableCell sx={{ fontWeight: 600 }} align="right">
              Success
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {ranked.map((r, idx) => {
            const success = asNumber(r.success_rate);
            const latency = asNumber(r.latency_ms);
            const cost = asNumber(r.est_cost_usd);
            const isChosen = idx === 0 && r.model === chosen;
            return (
              <TableRow key={`${r.provider}-${r.model}-${idx}`}>
                <TableCell sx={{ wordBreak: "break-all" }}>
                  <Stack direction="row" spacing={0.75} alignItems="center">
                    <Typography
                      variant="body2"
                      sx={{ fontFamily: GATEWAY_MONO, fontSize: "0.8rem" }}
                    >
                      {asText(r.model) || "—"}
                    </Typography>
                    {isChosen && (
                      <Chip
                        label="Chosen"
                        size="small"
                        color="success"
                        variant="outlined"
                      />
                    )}
                  </Stack>
                  {asText(r.provider) && (
                    <Typography variant="caption" color="text.secondary">
                      {providerLabel(r.provider, r.billing)}
                    </Typography>
                  )}
                </TableCell>
                <TableCell align="right">{fixed(r.tier)}</TableCell>
                <TableCell align="right">{fixed(r.score)}</TableCell>
                <TableCell align="right">
                  {cost == null ? "—" : formatCost(cost)}
                </TableCell>
                <TableCell align="right">
                  {latency ? formatLatency(latency) : "—"}
                </TableCell>
                <TableCell align="right">
                  {success ? `${(success * 100).toFixed(1)}%` : "—"}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

RankedTable.propTypes = {
  ranked: PropTypes.array.isRequired,
  chosen: PropTypes.string,
};

export function AutoRoutingSection({ log }) {
  const meta = log.metadata || {};
  const [showRejected, setShowRejected] = useState(false);
  const chosen = asText(meta.auto_model);
  if (!chosen) return null;

  const decision = parseAutoDecision(meta.auto_decision);
  const malformed =
    meta.auto_decision != null && meta.auto_decision !== "" && !decision;
  const requested = asText(meta.auto_requested) || "auto";
  const tier = asText(meta.auto_task_tier) || asText(decision?.task_tier);
  const complexity =
    asNumber(meta.auto_complexity) ?? asNumber(decision?.complexity);
  const preference =
    asText(meta.auto_preference) || asText(decision?.preference);
  const eligible = asNumber(meta.auto_eligible) ?? decision?.ranked.length;
  const considered =
    asNumber(meta.auto_considered) ?? asNumber(decision?.considered);
  const fallbacks = asList(meta.auto_fallbacks).length
    ? asList(meta.auto_fallbacks)
    : decision?.fallbacks || [];
  const needs = decision?.needs || [];
  // "Future AGI managed" for a managed choice: the
  // gateway marks how each candidate is paid for. A decision recorded before
  // it did carries no mark and keeps the provider id.
  const provider = asText(decision?.provider)
    ? providerLabel(decision.provider, decision.billing)
    : null;
  const reason = asText(meta.auto_reason);
  // The decision keeps a sample of at most 10 rejections.
  const rejected = decision?.rejected || [];
  const setAside =
    eligible != null && considered != null
      ? Math.max(considered - eligible, rejected.length)
      : rejected.length;

  return (
    <DetailSection title="Auto routing">
      <Box>
        <DetailRow label="Model">
          <Box component="span" sx={{ fontFamily: GATEWAY_MONO }}>
            {requested}
          </Box>{" "}
          <GatewayIcon
            name="arrowRight"
            size={14}
            aria-hidden
            sx={{ verticalAlign: "middle", color: "text.secondary" }}
          />{" "}
          <Box component="span" sx={{ fontFamily: GATEWAY_MONO }}>
            {chosen}
          </Box>
          {provider ? ` via ${provider}` : ""}
        </DetailRow>
        {tier && (
          <DetailRow label="Task">
            {tier}
            {complexity != null ? ` (complexity ${complexity})` : ""}
          </DetailRow>
        )}
        {preference && <DetailRow label="Preference">{preference}</DetailRow>}
        {(eligible != null || considered != null) && (
          <DetailRow label="Eligible">
            {eligible ?? "?"} of {considered ?? "?"} models considered
          </DetailRow>
        )}
        {needs.length > 0 && (
          <DetailRow label="Needs">
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
              {needs.map((n) => (
                <Chip key={n} label={n} size="small" variant="outlined" />
              ))}
            </Stack>
          </DetailRow>
        )}
        {fallbacks.length > 0 && (
          <DetailRow label="Fallbacks">
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
              {fallbacks.map((f) => (
                <Chip
                  key={f}
                  label={f}
                  size="small"
                  variant="outlined"
                  sx={{ fontFamily: GATEWAY_MONO, typography: "s3" }}
                />
              ))}
            </Stack>
          </DetailRow>
        )}
        <AutoContextRows log={log} decision={decision} />
        <PolicyRows routing={log.routing} />
        {/* The reason sentence restates the task, needs, preference and
            counts above; it stands in for them on logs that lack them. */}
        {reason && !tier && (
          <Typography typography="s2" color="text.secondary">
            {reason}
          </Typography>
        )}
      </Box>

      {decision?.ranked.length > 0 && (
        <RankedTable ranked={decision.ranked} chosen={chosen} />
      )}

      {rejected.length > 0 && (
        <Box mt={1}>
          <Button
            size="small"
            color="inherit"
            onClick={() => setShowRejected((v) => !v)}
            endIcon={
              <GatewayIcon
                name={showRejected ? "chevronUp" : "chevronDown"}
                size={16}
              />
            }
            sx={{ px: 0.5, color: "text.secondary" }}
          >
            {showRejected ? "Hide" : "Show"}{" "}
            {rejected.length < setAside
              ? `${rejected.length} of ${setAside} models set aside`
              : `${setAside} models set aside`}
          </Button>
          <Collapse in={showRejected}>
            <Stack spacing={0.5} mt={0.5}>
              {rejected.map((r, idx) => (
                <Typography
                  key={`${r.model}-${idx}`}
                  variant="body2"
                  color="text.secondary"
                >
                  <Box
                    component="span"
                    sx={{ fontFamily: GATEWAY_MONO, color: "text.primary" }}
                  >
                    {asText(r.model) || "—"}
                  </Box>
                  {": "}
                  {asText(r.reason) || "no reason given"}
                </Typography>
              ))}
            </Stack>
          </Collapse>
        </Box>
      )}

      {malformed && (
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          mt={1}
        >
          The full ranking for this request could not be read.
        </Typography>
      )}
    </DetailSection>
  );
}

AutoRoutingSection.propTypes = { log: PropTypes.object.isRequired };
