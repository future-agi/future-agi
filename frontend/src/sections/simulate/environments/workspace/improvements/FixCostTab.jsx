import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import { formatCredits } from "./fixRunView";

const num = { fontVariantNumeric: "tabular-nums" };

// The design's credit ledger, contract fields only: what was spent, what is
// held for check runs not yet billed, and the limit.
export default function FixCostTab({ fix }) {
  const held = Math.max(
    0,
    (fix.committed_credits ?? 0) - (fix.spent_credits ?? 0),
  );
  const rows = [
    {
      label: "Spent",
      note: "model calls and finished check runs, as billed",
      value: fix.spent_credits,
    },
    {
      label: "Held for running checks",
      note: "a worst-case hold for each check run not yet billed",
      value: held,
    },
    {
      label: "Limit",
      note: "the fix stops at this",
      value: fix.request?.budget_credits,
    },
  ];
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        maxWidth: 560,
      }}
    >
      {rows.map((r, i) => (
        <Stack
          key={r.label}
          direction="row"
          alignItems="center"
          sx={{
            px: 2,
            py: 1,
            ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {r.label}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {r.note}
            </Typography>
          </Box>
          <Typography sx={{ typography: "s2", fontWeight: 600, ...num }}>
            {formatCredits(r.value)}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
}

FixCostTab.propTypes = { fix: PropTypes.object.isRequired };
