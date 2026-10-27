import PropTypes from "prop-types";
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { creditsForUsd } from "../_mock/metering";

/*
  An improvement's cost, part by part — before it starts (the estimate's
  ceiling) and after (what it spent). The system table, like a run's Cost
  tab: one row per part, the conversations it runs and its credits, a total.
*/
const num = {
  typography: "s3",
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};
const tableSx = {
  "& thead th": { whiteSpace: "nowrap" },
  "& tbody tr": { height: 36 },
  "& tbody td": { py: 0 },
};
const fmt = (n) => Math.round(n || 0).toLocaleString();

export default function CostPartsTable({ parts, totalLabel, note, totalUsd }) {
  if (!parts?.length) return null;
  /* A total someone else already showed (an estimate's headline) wins, so
     the two never read differently. */
  const total =
    totalUsd != null
      ? creditsForUsd(totalUsd)
      : parts.reduce((a, p) => a + creditsForUsd(p.usd || 0), 0);
  return (
    <Box>
      <Table size="small" sx={tableSx}>
        <TableHead>
          <TableRow>
            <TableCell>Part</TableCell>
            <TableCell sx={{ textAlign: "right" }}>Conversations</TableCell>
            <TableCell sx={{ textAlign: "right" }}>Credits</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {parts.map((p) => (
            <TableRow key={p.id}>
              <TableCell sx={{ typography: "s2", minWidth: 0 }}>
                <Box component="span" sx={{ color: "text.primary" }}>
                  {p.label}
                </Box>
                {p.detail && (
                  <Box
                    component="span"
                    sx={{ color: "text.tertiary", typography: "s3" }}
                  >
                    {` · ${p.detail}`}
                  </Box>
                )}
              </TableCell>
              <TableCell sx={{ ...num, color: "text.secondary" }}>
                {p.conversations == null ? "—" : fmt(p.conversations)}
              </TableCell>
              <TableCell sx={{ ...num, color: "text.secondary" }}>
                {fmt(creditsForUsd(p.usd || 0))}
              </TableCell>
            </TableRow>
          ))}
          <TableRow>
            <TableCell
              sx={{ typography: "s2", fontWeight: 500, color: "text.primary" }}
            >
              {totalLabel}
            </TableCell>
            <TableCell />
            <TableCell sx={{ ...num, fontWeight: 600, color: "text.primary" }}>
              {fmt(total)}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
      {note && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          {note}
        </Typography>
      )}
    </Box>
  );
}

CostPartsTable.propTypes = {
  /* [{ id, label, detail, conversations, usd }] */
  parts: PropTypes.array,
  totalLabel: PropTypes.string,
  note: PropTypes.string,
  totalUsd: PropTypes.number,
};
