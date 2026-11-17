// IT-R1 G2 P2: `Compare any runs…` turned on. The checkbox column is
// highlighted (LeaderboardGrid, `context.compareAny`) and this line says what
// to do, with kind chips that filter the table to one kind of run (the
// leaderboard's own Kind filter, so it shows in Filter and is undoable).
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Chip, Stack, Typography } from "@mui/material";
import { kindOfRun } from "./leaderboardModel";

// The order the chips read in (labels from common/Runs/runKind).
const KIND_ORDER = [
  "Your code",
  "Harness",
  "Prompt",
  "Prompt + tools",
  "Agent",
  "Voice agent",
  "Endpoint",
  "Traces",
  "Upload",
];
/** Plain words for the chip (`Agent` is an agent graph). */
const CHIP_TEXT = { Agent: "Agent graph", "Voice agent": "Voice" };

const kindFilterOf = (filters) =>
  (filters ?? []).find((f) => f?.column_id === "kind")?.filter_config
    ?.filter_value?.[0] ?? null;

export default function CompareAnyHint({
  runs,
  filters,
  onFilters,
  selectedCount = 0,
}) {
  const kinds = useMemo(() => {
    const present = new Set((runs ?? []).map(kindOfRun).filter(Boolean));
    return KIND_ORDER.filter((k) => present.has(k));
  }, [runs]);
  const active = kindFilterOf(filters);
  const setKind = (k) => {
    const rest = (filters ?? []).filter((f) => f?.column_id !== "kind");
    onFilters(
      k
        ? [
            ...rest,
            {
              column_id: "kind",
              filter_config: {
                filter_type: "option",
                filter_op: "equals",
                filter_value: [k],
              },
            },
          ]
        : rest,
    );
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      flexWrap="wrap"
      data-testid="compare-any-hint"
      sx={{ px: 1.5, pl: 4.75, pb: 0.75 }}
    >
      <Typography typography="s2" fontWeight={500} color="primary.main">
        {selectedCount >= 2
          ? `${selectedCount} ticked · use Compare ${selectedCount} runs below`
          : "Tick 2–6 runs from any group"}
      </Typography>
      <Typography typography="s2" color="text.secondary">
        Show:
      </Typography>
      <Chip
        size="small"
        label="All kinds"
        variant={active ? "outlined" : "filled"}
        color={active ? "default" : "primary"}
        onClick={() => setKind(null)}
      />
      {kinds.map((k) => (
        <Chip
          key={k}
          size="small"
          label={CHIP_TEXT[k] ?? k}
          variant={active === k ? "filled" : "outlined"}
          color={active === k ? "primary" : "default"}
          onClick={() => setKind(active === k ? null : k)}
        />
      ))}
    </Stack>
  );
}

CompareAnyHint.propTypes = {
  runs: PropTypes.array,
  filters: PropTypes.array,
  onFilters: PropTypes.func.isRequired,
  selectedCount: PropTypes.number,
};
