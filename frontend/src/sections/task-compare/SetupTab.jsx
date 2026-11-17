import React from "react";
import PropTypes from "prop-types";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";
import { provenanceOf } from "src/sections/common/Runs/provenanceModel";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import { runModelsText } from "src/sections/common/Runs/runLabel";
import { setupRows } from "src/sections/common/CompareRuns/setupRows";
import { runInfoRows } from "./LaneHeader";
import { laneNames } from "./storyGrid";

/**
 * IT-R5 G4 P4: Inspect's first tab, `Setup`: each lane's model, framework
 * or harness with its version, agent or prompt version, tools by name and
 * MCP servers, side by side; a value that differs from the baseline's is
 * bold. Blind lanes say so (names would give the run away). W5 B6: the rows
 * and what differs come from CompareRuns/setupRows (the one config diff,
 * the rows Explore › Config lists), with a `Differences only` switch.
 */
export default function SetupTab({ lanes, runOf }) {
  const [diffOnly, setDiffOnly] = React.useState(false);
  const cols = lanes.map((l) => {
    if (l.blindName) return { lane: l, run: null, info: null };
    const run = runOf?.(l.id);
    const prov = run ? provenanceOf(run) : null;
    const info = runInfoRows(prov, runModelsText(run) || null);
    return { lane: l, run, info, entryId: prov?.source?.entryId ?? null };
  });
  const model = setupRows(cols);
  const names = laneNames(lanes);
  const keys = diffOnly ? model.rows.filter((r) => r.changed) : model.rows;
  if (!model.rows.length)
    return (
      <Typography typography="s2" color="text.secondary" sx={{ p: 1.5 }}>
        These runs do not say which model or tools they used.
      </Typography>
    );
  return (
    <Box
      data-testid="tc-setup"
      data-count={model.count}
      sx={{
        display: "grid",
        gridTemplateColumns: `88px repeat(${cols.length}, minmax(0, 1fr))`,
        columnGap: 1.5,
        rowGap: 0.5,
        p: 1.5,
        alignItems: "baseline",
      }}
    >
      <FormControlLabel
        sx={{ gridColumn: "1 / -1", m: 0 }}
        control={
          <Switch
            size="small"
            checked={diffOnly}
            onChange={(e) => setDiffOnly(e.target.checked)}
            inputProps={{ "aria-label": "Differences only" }}
          />
        }
        label={
          <Typography typography="s2">
            {`Differences only (${model.count})`}
            {model.notCompared.length
              ? ` · ${model.notCompared.map((i) => cols[i].lane.label).join(", ")} recorded no settings to compare`
              : ""}
          </Typography>
        }
      />
      <Box />
      {cols.map(({ lane }) => (
        <Typography
          key={lane.id}
          typography="s2"
          fontWeight={600}
          noWrap
          sx={{
            borderTop: "3px solid",
            borderColor: lane.color?.solid,
            pt: 0.5,
          }}
        >
          {lane.blindName ??
            [lane.label, names.get(lane.id)].filter(Boolean).join(" ")}
        </Typography>
      ))}
      {keys.map(({ key, name: k, values, differs: changedAt }) => (
        <React.Fragment key={key}>
          <Typography typography="s3" color="text.secondary">
            {k}
          </Typography>
          {cols.map(({ lane, info, entryId }, i) => {
            const v = values[i] ?? null;
            const differs = changedAt[i];
            return (
              <Typography
                key={lane.id}
                typography="s2"
                data-setup-cell={`${k}:${differs ? "differs" : "same"}`}
                color={v ? "text.primary" : "text.disabled"}
                fontWeight={differs ? 600 : 400}
                sx={{ overflowWrap: "anywhere" }}
              >
                {k === "Framework" && v && entryId ? (
                  // The same mark as the lane header, before the name.
                  <Box
                    component="span"
                    sx={{
                      display: "inline-flex",
                      verticalAlign: "-3px",
                      mr: 0.5,
                    }}
                  >
                    <SourceLogo entryId={entryId} size={16} />
                  </Box>
                ) : null}
                {info ? v ?? "—" : "Hidden in blind review"}
              </Typography>
            );
          })}
        </React.Fragment>
      ))}
    </Box>
  );
}

SetupTab.propTypes = {
  lanes: PropTypes.array.isRequired,
  runOf: PropTypes.func,
};
