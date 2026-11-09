import React, { useId, useMemo, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Skeleton from "@mui/material/Skeleton";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { GatewayFilterBar } from "src/sections/gateway/components/frame";

import { downloadCreditCsv, useCreditStatement } from "./api";
import {
  balanceTooltip,
  formatAmount,
  formatBalance,
  monthLabel,
  recentMonths,
} from "./format";

const CREDIT_LABELS = {
  topup: "Top-ups",
  grant: "Grants",
  refund: "Refunds",
  adjustment: "Adjustments",
  expiry: "Expiries",
};

// A calendar month (UTC) of the ledger: opening balance, credits by kind,
// spend by model with its markup, closing balance.
export default function CreditStatement() {
  const titleId = useId();
  const months = useMemo(() => recentMonths(36), []);
  const [month, setMonth] = useState(months[0]);
  const { data, isLoading, isError } = useCreditStatement(month);

  const exportCsv = () =>
    downloadCreditCsv(
      "statement",
      { month },
      `ai-gateway-credit-statement-${month}.csv`,
    ).catch(() =>
      enqueueSnackbar("The statement could not be exported. Try again.", {
        variant: "error",
      }),
    );

  const credits = Object.entries(data?.credits_by_kind || {}).filter(
    ([, amount]) => Number(amount) !== 0,
  );

  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Typography id={titleId} typography="s1" fontWeight="fontWeightMedium">
        Monthly statement
      </Typography>
      <GatewayFilterBar
        actions={
          <Button variant="outlined" size="small" onClick={exportCsv}>
            Export CSV
          </Button>
        }
      >
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
      </GatewayFilterBar>
      {isLoading && <Skeleton variant="rounded" height={160} />}
      {isError && (
        <Typography typography="s1" color="text.secondary" role="alert">
          The statement could not be loaded.
        </Typography>
      )}
      {data && (
        <TableContainer
          sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
        >
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Line</TableCell>
                <TableCell align="right">Requests</TableCell>
                <TableCell align="right">Amount</TableCell>
                <TableCell
                  align="right"
                  sx={{ display: { xs: "none", sm: "table-cell" } }}
                >
                  Markup
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              <TableRow>
                <TableCell>Opening balance</TableCell>
                <TableCell />
                <TableCell
                  align="right"
                  title={balanceTooltip(data.opening_balance) ?? undefined}
                >
                  {formatBalance(data.opening_balance)}
                </TableCell>
                <TableCell sx={{ display: { xs: "none", sm: "table-cell" } }} />
              </TableRow>
              {credits.map(([kind, amount]) => (
                <TableRow key={kind}>
                  <TableCell>{CREDIT_LABELS[kind] || kind}</TableCell>
                  <TableCell />
                  <TableCell align="right">{formatAmount(amount)}</TableCell>
                  <TableCell
                    sx={{ display: { xs: "none", sm: "table-cell" } }}
                  />
                </TableRow>
              ))}
              {data.spend_by_model.map((line) => (
                <TableRow key={`${line.model || "unknown"}-${Boolean(line.estimated)}`}>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>
                    {line.model || "Unknown model"}{line.estimated ? " (estimated)" : ""}
                  </TableCell>
                  <TableCell align="right">{line.count}</TableCell>
                  <TableCell align="right">
                    {formatAmount(-Number(line.spend_usd))}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{ display: { xs: "none", sm: "table-cell" } }}
                  >
                    {formatAmount(line.markup_usd)}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow sx={{ "& td": { borderBottom: 0, fontWeight: 600 } }}>
                <TableCell>Closing balance</TableCell>
                <TableCell />
                <TableCell
                  align="right"
                  title={balanceTooltip(data.closing_balance) ?? undefined}
                >
                  {formatBalance(data.closing_balance)}
                </TableCell>
                <TableCell sx={{ display: { xs: "none", sm: "table-cell" } }} />
              </TableRow>
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
