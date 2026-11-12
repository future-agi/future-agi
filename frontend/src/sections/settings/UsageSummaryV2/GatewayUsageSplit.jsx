import React, { useId, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";
import {
  useCreditStatement,
  useGatewayCreditSummary,
} from "src/sections/settings/BillingV2/gateway-credits/api";
import {
  MANAGED_MODE_LABEL,
  OWN_KEY_LABEL,
} from "src/sections/gateway/utils/providerLabel";
import {
  formatAmount,
  monthLabel,
  recentMonths,
} from "src/sections/settings/BillingV2/gateway-credits/format";

import { splitGatewayUsage } from "./gateway-usage";

function monthRange(month) {
  const [year, mm] = month.split("-").map(Number);
  return {
    start: new Date(Date.UTC(year, mm - 1, 1)).toISOString(),
    end: new Date(Date.UTC(year, mm, 1)).toISOString(),
  };
}

function Figure({ label, value, caption, note, title }) {
  return (
    <Box sx={{ minWidth: 0, flex: 1 }}>
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
      <Typography typography="m2" color="text.primary" noWrap title={title}>
        {value}
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {caption}
      </Typography>
      {note && (
        <Typography typography="s2" color="text.secondary">
          {note}
        </Typography>
      )}
    </Box>
  );
}

Figure.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  caption: PropTypes.node,
  note: PropTypes.string,
  title: PropTypes.string,
};

export default function GatewayUsageSplit({ now }) {
  const titleId = useId();
  const months = useMemo(() => recentMonths(12, now || new Date()), [now]);
  const [month, setMonth] = useState(months[0]);
  // A failed statement shows "Managed spend unavailable" with Retry below,
  // so it must not also raise the app-wide error toast.
  const statement = useCreditStatement(month, { errorHandled: true });
  const range = monthRange(month);
  const requestCost = useQuery({
    queryKey: ["gateway-usage-split", "billing-mode-cost", month],
    queryFn: () =>
      axios.get(endpoints.gateway.analyticsCost, {
        params: { ...range, group_by: "billing_mode" },
      }),
    select: (res) => res.data?.result ?? null,
    retry: false,
  });

  // Spend charged at the gateway and not yet in the ledger, from the credit
  // summary Billing reads. It is today's figure, not a month's, so only the
  // current month names it, in Billing's words (Spent, settling), and the
  // ledger figure above it is unchanged.
  const summary = useGatewayCreditSummary();
  const settling =
    month === months[0] ? Number(summary.data?.pending_settlement_usd || 0) : 0;

  const loading = statement.isLoading || requestCost.isLoading;
  const split = splitGatewayUsage(statement.data, requestCost.data);
  const managedAvailable =
    split.managedSpend !== null &&
    split.managedRequests !== null &&
    !statement.isError;
  const ownKeyAvailable = split.ownKeyCost !== null && !requestCost.isError;
  const total =
    managedAvailable && ownKeyAvailable
      ? split.managedSpend + split.ownKeyCost
      : 0;
  const managedShare = total > 0 ? (split.managedSpend / total) * 100 : 0;

  return (
    <Paper
      variant="outlined"
      component="section"
      aria-labelledby={titleId}
      sx={{
        p: { xs: 2, sm: 3 },
        borderRadius: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
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
            AI Gateway usage
          </Typography>
          <Typography typography="s1" color="text.secondary">
            Model spend through the gateway, by who bills it.
          </Typography>
        </Box>
        <TextField
          select
          size="small"
          label="Month"
          value={month}
          onChange={(event) => setMonth(event.target.value)}
          sx={{ minWidth: 180 }}
        >
          {months.map((value) => (
            <MenuItem key={value} value={value}>
              {monthLabel(value)}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      {loading ? (
        <Skeleton variant="rounded" height={96} />
      ) : (
        <>
          <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
            <Figure
              label={MANAGED_MODE_LABEL}
              value={
                managedAvailable
                  ? formatAmount(split.managedSpend)
                  : "Managed spend unavailable"
              }
              title={managedAvailable ? `${split.managedSpend} USD` : undefined}
              note={
                managedAvailable && settling > 0
                  ? `+ ${formatAmount(settling)} settling: charged at the gateway, not yet in the ledger`
                  : undefined
              }
              caption={
                managedAvailable ? (
                  `${split.managedRequests.toLocaleString("en-US")} requests, billed by Future AGI from your credit`
                ) : (
                  <Button
                    size="small"
                    onClick={() => statement.refetch()}
                    disabled={statement.isFetching}
                    // No side padding: the label lines up with the line above.
                    sx={{ px: 0, minWidth: 0 }}
                  >
                    Retry managed spend
                  </Button>
                )
              }
            />
            {ownKeyAvailable && (
              <Figure
                label={OWN_KEY_LABEL}
                value={formatAmount(split.ownKeyCost)}
                title={`${split.ownKeyCost} USD`}
                caption="Billed by your providers directly"
              />
            )}
          </Stack>
          {managedAvailable && ownKeyAvailable && total > 0 && (
            <Box
              role="img"
              aria-label={`${MANAGED_MODE_LABEL} ${Math.round(managedShare)}% of gateway spend`}
              sx={{
                display: "flex",
                height: 8,
                borderRadius: 1,
                overflow: "hidden",
                bgcolor: "action.selected",
              }}
            >
              <Box
                sx={{
                  width: `${managedShare}%`,
                  bgcolor: "primary.main",
                }}
              />
            </Box>
          )}
          <Typography typography="s2" color="text.secondary">
            {managedAvailable
              ? `Future AGI credits are the ledger's exact spend for ${monthLabel(month)}: the amounts your requests were charged.`
              : `Managed ledger spend is unavailable for ${monthLabel(month)}.`}{" "}
            Own-key cost is what the gateway logged for requests on your own
            provider keys that month.{" "}
            <Link
              component={RouterLink}
              href={`${paths.dashboard.settings.billing}#ai-gateway-credits`}
            >
              Ledger and statement
            </Link>
          </Typography>
        </>
      )}
    </Paper>
  );
}

GatewayUsageSplit.propTypes = {
  now: PropTypes.instanceOf(Date),
};
