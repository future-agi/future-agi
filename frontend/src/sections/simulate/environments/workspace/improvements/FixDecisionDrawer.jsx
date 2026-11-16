import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import SideDrawer from "../../components/SideDrawer";

import CheckLine from "./CheckLine";
import FixStatusChip from "./FixStatusChip";
import {
  checksLine,
  fixName,
  headline,
  pct,
  pts,
  describeChecks,
  oldVerdictLine,
  rejectionChecks,
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

// One check and, for a tier that holds several (such as each ceiling), what each
// one verified.
function CheckRows({ check, sx }) {
  return (
    <>
      <CheckLine check={check} nameWidth={120} sx={sx} />
      {(check.parts || []).map((part) => (
        <CheckLine
          key={part.name}
          check={part}
          nameWidth={120}
          sx={{ pl: 3, mt: 0.5 }}
        />
      ))}
    </>
  );
}
CheckRows.propTypes = {
  check: PropTypes.object.isRequired,
  sx: PropTypes.object,
};

// "How it was decided", laid out as the design's decision drawer: the evidence
// for the kept trial, every finalist side by side, and the checks it passed.
// When no trial was kept, each rejected trial shows the check that rejected it.
export default function FixDecisionDrawer({ fix, open, onClose }) {
  const winner = winnerOf(fix);
  const finalists = (fix.candidates || [])
    .filter((c) => c.outcome === "winner" || c.outcome === "finalist")
    .sort((a, b) => a.attempt - b.attempt);
  const rejected = winner
    ? []
    : (fix.candidates || [])
        .filter((c) => c.outcome === "rejected" && c.verdict)
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
            ? `${fixName(fix)} · Candidate ${winner.attempt} kept`
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
                {["Candidate", "Target", "Held back", "Result"].map((h) => (
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
                  >{`Candidate ${c.attempt}`}</Typography>
                  <Typography sx={{ typography: "s3", ...num }}>
                    {c.verdict.target_pass_rate
                      ? `${pct(c.verdict.target_pass_rate[1])}%`
                      : "Pending"}
                  </Typography>
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
        {v?.tiers?.length > 0 && (
          <Box>
            <Typography sx={sectionTitle}>Checks</Typography>
            <Box sx={table}>
              <Box sx={{ ...rowSx(0), gridTemplateColumns: "1fr" }}>
                <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                  {checksLine(v.tiers)}
                </Typography>
              </Box>
              {describeChecks(v.tiers, fix.request, v, fix.plan).map((t, i) => (
                <Box key={t.name} sx={rowSx(i + 1)}>
                  <CheckRows check={t} />
                </Box>
              ))}
            </Box>
          </Box>
        )}
        {rejected.length > 0 && (
          <Box>
            <Typography sx={sectionTitle}>Rejected candidates</Typography>
            <Box sx={table}>
              {rejected.map((c, i) => (
                <Box
                  key={c.id}
                  data-testid={`rejected-${c.attempt}`}
                  sx={{ ...rowSx(i), gridTemplateColumns: "1fr" }}
                >
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}
                  >{`Candidate ${c.attempt}`}</Typography>
                  {c.verdict.tiers?.length > 0 ? (
                    rejectionChecks(
                      c.verdict.tiers,
                      fix.request,
                      c.verdict,
                      fix.plan,
                    ).map((t) => (
                      <CheckRows key={t.name} check={t} sx={{ mt: 0.25 }} />
                    ))
                  ) : (
                    // A verdict from before checks were recorded: its numbers in words.
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {oldVerdictLine(c.verdict)}
                    </Typography>
                  )}
                </Box>
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
