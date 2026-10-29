import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import { useLocation } from "react-router-dom";
import { Box, Link, Stack, Typography } from "@mui/material";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { runColor } from "../runs/runs.constants";

import { OUTCOME_LABEL, pct, pts } from "./fixRunView";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";

const GRID = "minmax(280px, 380px) 200px 160px 1fr";
const num = { fontVariantNumeric: "tabular-nums" };

const head = { typography: "s3", fontWeight: 600, color: "text.subtitle" };

function PassCell({ rate }) {
  const delta = pts(rate);
  return (
    <Stack direction="row" spacing={1} alignItems="baseline">
      <Typography
        sx={{ typography: "s2", fontWeight: 600, ...num }}
      >{`${pct(rate[1])}%`}</Typography>
      {delta !== 0 && (
        <Typography
          sx={{
            typography: "s3",
            ...num,
            color: delta > 0 ? BUILD_TONES.green : BUILD_TONES.red,
          }}
        >
          {`${delta > 0 ? "↑" : "↓"} ${Math.abs(delta)} pts`}
        </Typography>
      )}
    </Stack>
  );
}
PassCell.propTypes = { rate: PropTypes.arrayOf(PropTypes.number).isRequired };

// The design's trials table, contract columns only: what each trial tried,
// how it ended, and its pass rate on the target scenarios.
export default function TrialsTable({ fix }) {
  const location = useLocation();
  const trials = [...(fix.candidates || [])].sort(
    (a, b) => a.attempt - b.attempt,
  );
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: GRID,
          px: 2,
          py: 1,
          bgcolor: "background.neutral",
        }}
      >
        <Typography sx={head}>Trial</Typography>
        <Typography sx={head}>Status</Typography>
        <Typography sx={head}>Pass</Typography>
      </Box>
      {trials.map((c) => {
        const live = c.outcome === "pending" ? c.check_runs?.[0] : null;
        return (
          <Box
            key={c.id}
            data-testid={`trial-${c.attempt}`}
            sx={{
              display: "grid",
              gridTemplateColumns: GRID,
              alignItems: "center",
              px: 2,
              py: 1,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Stack
              direction="row"
              spacing={1.25}
              alignItems="center"
              minWidth={0}
            >
              <Box
                sx={{
                  px: 0.75,
                  borderRadius: 0.75,
                  typography: "s3",
                  fontWeight: 700,
                  color: runColor(c.attempt),
                  bgcolor: (t) =>
                    alpha(
                      runColor(c.attempt),
                      t.palette.mode === "dark" ? 0.22 : 0.14,
                    ),
                  flexShrink: 0,
                }}
              >
                {`T${c.attempt}`}
              </Box>
              <Box minWidth={0}>
                <Typography
                  sx={{ typography: "s2", fontWeight: 600 }}
                >{`Trial ${c.attempt}`}</Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  {`Tried: ${c.summary}`}
                </Typography>
              </Box>
            </Stack>
            {live ? (
              <Link
                component={RouterLink}
                href={paths.dashboard.simulate.environments.execution(
                  fix.environment_id,
                  live.run_id,
                  live.test_execution_id,
                )}
                state={{ backTo: location.pathname }}
                sx={{ typography: "s2" }}
              >
                Running · Watch live
              </Link>
            ) : (
              <Typography sx={{ typography: "s2" }}>
                {OUTCOME_LABEL[c.outcome] || c.outcome}
              </Typography>
            )}
            <PassCell rate={c.verdict.target_pass_rate} />
          </Box>
        );
      })}
    </Box>
  );
}

TrialsTable.propTypes = { fix: PropTypes.object.isRequired };
