import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import SideDrawer from "../../components/SideDrawer";

import FixStatusChip from "./FixStatusChip";
import {
  checksLine,
  fixName,
  headline,
  pct,
  pts,
  winnerOf,
} from "./fixRunView";

const num = { fontVariantNumeric: "tabular-nums" };
const sectionTitle = { typography: "s2", fontWeight: 600, mb: 1 };
const table = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  overflow: "hidden",
};
const rowSx = (i) => ({
  display: "grid",
  alignItems: "center",
  px: 1.5,
  py: 0.75,
  ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
});

const rate = (r) => `${pct(r[0])}% → ${pct(r[1])}%`;
const gain = (r) => {
  const d = pts(r);
  return d ? `${d > 0 ? "↑" : "↓"} ${Math.abs(d)} pts` : "";
};

// "How it was decided", laid out as the design's decision drawer: the evidence
// for the kept trial, every finalist side by side, and the checks it passed.
export default function FixDecisionDrawer({ fix, open, onClose }) {
  const winner = winnerOf(fix);
  const finalists = (fix.candidates || [])
    .filter((c) => c.outcome === "winner" || c.outcome === "finalist")
    .sort((a, b) => a.attempt - b.attempt);
  const v = winner?.verdict;
  const evidence = v
    ? [
        v.held_back_pass_rate && {
          label: "Held-back scenarios",
          value: rate(v.held_back_pass_rate),
          change: gain(v.held_back_pass_rate),
        },
        {
          label: "Target scenarios",
          value: rate(v.target_pass_rate),
          change: gain(v.target_pass_rate),
        },
        v.held_back_pass_rate && {
          label: "Overfit gap",
          value: `${pct(v.target_pass_rate[1]) - pct(v.held_back_pass_rate[1])} pts`,
          change: "",
        },
        {
          label: "Must-pass scenarios broken",
          value: String(v.must_pass_regressions.length),
          change: "",
        },
      ].filter(Boolean)
    : [];
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Box data-testid="fix-decision" sx={{ p: 2.5, pr: 5 }}>
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          How it was decided
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 2 }}>
          {winner
            ? `${fixName(fix)} · Trial ${winner.attempt} kept`
            : fixName(fix)}
        </Typography>
        <Box sx={{ ...table, p: 1.5, mb: 2 }}>
          <Box sx={{ display: "inline-flex" }}>
            <FixStatusChip state={fix.state} />
          </Box>
          {headline(fix) && (
            <Typography sx={{ typography: "s2", fontWeight: 600, mt: 1 }}>
              {headline(fix)}
            </Typography>
          )}
        </Box>
        {evidence.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography sx={sectionTitle}>Evidence</Typography>
            <Box sx={table}>
              {evidence.map((e, i) => (
                <Box
                  key={e.label}
                  sx={{ ...rowSx(i), gridTemplateColumns: "1fr 160px 90px" }}
                >
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {e.label}
                  </Typography>
                  <Typography sx={{ typography: "s3", ...num }}>
                    {e.value}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      ...num,
                      color: BUILD_TONES.green,
                      textAlign: "right",
                    }}
                  >
                    {e.change}
                  </Typography>
                </Box>
              ))}
            </Box>
          </Box>
        )}
        {finalists.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography sx={sectionTitle}>Finalists</Typography>
            <Box sx={table}>
              <Box
                sx={{ ...rowSx(0), gridTemplateColumns: "1fr 90px 90px 80px" }}
              >
                {["Run", "Target", "Held back", "Result"].map((h) => (
                  <Typography
                    key={h}
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: "text.subtitle",
                    }}
                  >
                    {h}
                  </Typography>
                ))}
              </Box>
              {finalists.map((c) => (
                <Box
                  key={c.id}
                  data-testid={`finalist-${c.attempt}`}
                  sx={{
                    ...rowSx(1),
                    gridTemplateColumns: "1fr 90px 90px 80px",
                  }}
                >
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600 }}
                  >{`Trial ${c.attempt}`}</Typography>
                  <Typography
                    sx={{ typography: "s3", ...num }}
                  >{`${pct(c.verdict.target_pass_rate[1])}%`}</Typography>
                  <Typography sx={{ typography: "s3", ...num }}>
                    {c.verdict.held_back_pass_rate
                      ? `${pct(c.verdict.held_back_pass_rate[1])}%`
                      : "—"}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: c.outcome === "winner" ? 600 : 400,
                    }}
                  >
                    {c.outcome === "winner" ? "Kept" : "Passed"}
                  </Typography>
                </Box>
              ))}
            </Box>
          </Box>
        )}
        {v && (
          <Box>
            <Typography sx={sectionTitle}>Checks</Typography>
            <Box sx={table}>
              <Box sx={{ ...rowSx(0), gridTemplateColumns: "1fr" }}>
                <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                  {checksLine(v.tiers)}
                </Typography>
              </Box>
              {v.tiers.map((t, i) => (
                <Stack
                  key={t.name}
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  sx={{ ...rowSx(i + 1), display: "flex" }}
                >
                  <Iconify
                    icon={
                      t.status === "failed"
                        ? "solar:close-circle-bold"
                        : "solar:check-circle-linear"
                    }
                    width={14}
                    sx={{
                      color:
                        t.status === "failed"
                          ? BUILD_TONES.red
                          : "text.secondary",
                    }}
                  />
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600, width: 90 }}
                  >
                    {t.name}
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {t.detail}
                  </Typography>
                </Stack>
              ))}
            </Box>
          </Box>
        )}
      </Box>
    </SideDrawer>
  );
}

FixDecisionDrawer.propTypes = {
  fix: PropTypes.object.isRequired,
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
