import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import SoonChip from "../../../components/SoonChip";

const versionNumber = (v) => {
  const m = /\d+(\.\d+)?/.exec(String(v));
  return m ? Number(m[0]) : null;
};

// The agent versions the runs used, newest first, with how many runs each.
// A version that reads as a number sorts by it; the rest keep the order the
// newest-first runs list met them in.
export function agentVersionsOf(runs = []) {
  const counts = new Map();
  runs.forEach((r) => {
    if (!r?.agentVersion) return;
    counts.set(r.agentVersion, (counts.get(r.agentVersion) || 0) + 1);
  });
  return [...counts.entries()]
    .map(([label, count], seen) => ({ label, count, seen }))
    .sort((a, b) => {
      const na = versionNumber(a.label);
      const nb = versionNumber(b.label);
      if (na != null && nb != null && na !== nb) return nb - na;
      return a.seen - b.seen;
    });
}

const runsText = (n) => `${n} ${n === 1 ? "run" : "runs"}`;

// The scope the Runs tab reads at: one agent version, so every number on the
// tab (headline, chart, table) reads that version's runs alone. Runs carry
// the environment version they were pinned to once versioning is on, but a
// select by it isn't built yet, so it is a soon chip.
export default function AgentVersionScope({ runs, value, onChange, shown }) {
  const versions = useMemo(() => agentVersionsOf(runs), [runs]);
  if (!runs.length) return null;
  const chips = [
    { key: null, text: "All", count: runs.length },
    ...versions.map((v) => ({ key: v.label, text: v.label, count: v.count })),
  ];

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      useFlexGap
      sx={{ flexWrap: "wrap", rowGap: 1, pb: 1.5, flexShrink: 0 }}
    >
      <Typography sx={{ typography: "s2", color: "text.subtitle", mr: 0.25 }}>
        Agent version
      </Typography>
      <Stack
        role="radiogroup"
        aria-label="Agent version"
        direction="row"
        spacing={1}
        useFlexGap
        sx={{ flexWrap: "wrap", rowGap: 1 }}
      >
        {chips.map((c) => {
          const on = value === c.key;
          const pick = () => onChange(c.key);
          return (
            <Box
              key={c.key ?? "all"}
              role="radio"
              aria-checked={on}
              aria-label={`${c.text}, ${runsText(c.count)}`}
              tabIndex={0}
              onClick={pick}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  pick();
                }
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.625,
                height: 28,
                px: 1.25,
                borderRadius: "4px",
                cursor: "pointer",
                border: "1px solid",
                borderColor: on ? "text.primary" : "divider",
                bgcolor: on ? "action.selected" : "transparent",
                "&:hover": {
                  borderColor: on ? "text.primary" : "text.disabled",
                },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: 1,
                },
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: on ? "fontWeightSemiBold" : "fontWeightMedium",
                }}
              >
                {c.text}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {c.count}
              </Typography>
            </Box>
          );
        })}
      </Stack>
      <SoonChip
        label="Environment version" detail="Which environment version each run used, so you can filter to runs on the same world and scenarios."
        what="The environment version each run used"
        sx={{ ml: 1 }}
      />
      {value != null && (
        <Typography
          sx={{ typography: "s3", color: "text.subtitle" }}
        >{`${shown} of ${runsText(runs.length)}`}</Typography>
      )}
    </Stack>
  );
}

AgentVersionScope.propTypes = {
  // Every run of the environment (up to the all-runs cap), newest first.
  runs: PropTypes.arrayOf(PropTypes.object).isRequired,
  // The agent version in scope; null for all.
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  // How many runs the scope keeps.
  shown: PropTypes.number,
};
