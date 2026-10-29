import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

import { formatCredits } from "./fixRunView";

const num = { fontVariantNumeric: "tabular-nums" };

// The design's credit strip, contract fields only and nothing computed: what
// was spent, what is committed (spent plus holds for unbilled check runs), and
// the budget.
export default function FixCostTab({ fix }) {
  const spent = fix.spent_credits ?? 0;
  const committed = fix.committed_credits ?? spent;
  const cells = [
    { label: "Spent", value: spent },
    { label: "Committed", value: committed },
    { label: "Budget", value: fix.request?.budget_credits },
  ];
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        p: 1,
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            md: "repeat(3, minmax(0, 1fr))",
          },
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          "& > *:not(:first-of-type)": {
            borderLeft: "1px solid",
            borderColor: "divider",
          },
        }}
      >
        {cells.map((c) => (
          <Box
            key={c.label}
            data-testid={`cost-${c.label}`}
            sx={{ px: 1.5, py: 1 }}
          >
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {c.label}
            </Typography>
            <Typography sx={{ typography: "s2", fontWeight: 600, ...num }}>
              {formatCredits(c.value)}
            </Typography>
          </Box>
        ))}
      </Box>
      <Typography
        sx={{ typography: "s3", color: "text.subtitle", px: 0.5, pt: 1 }}
      >
        {`Spent is the engine's model calls and finished check runs, as billed. Committed adds a worst-case hold for each check run not yet billed.`}
      </Typography>
    </Box>
  );
}

FixCostTab.propTypes = { fix: PropTypes.object.isRequired };
