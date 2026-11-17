// options-truth L5 G3: an endpoint, A2A or MCP run's own limits on its
// card: tries per row (the run plays this many tries of every row instead
// of the experiment's Trials) and a cost cap for the whole run (no per-try
// cap exists for a run Future AGI calls; the cap counts judge spend, W1-6).
// The create body carries them as
// the entry's `limits` (RunExperiment bodyOf); every count and estimate
// reads them through estimateInputs.ownTriesOf.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Stack, TextField, Typography } from "@mui/material";
import { Controller } from "react-hook-form";
import { capText } from "./reviewModel";
import { useCardValue } from "./cardWatch-g2";
import { clampTries, MAX_TRIES_PER_ROW, toPositive } from "./endpointCards-g2";

export default function EndpointLimitsRow({ control, fieldPrefix }) {
  const [refused, setRefused] = useState(null);
  const tries = useCardValue(control, fieldPrefix, "limits.tries");
  const cap = useCardValue(control, fieldPrefix, "limits.maxCostUsd");
  // L7c merge (result:endpoint-estimate, g3 owed/05; walk sk-e E3): the
  // card does not know the run's scope (a split or sample), so it names no
  // try count; Review says how many tries the cap lets run (≈k of N).
  const capLine = capText(cap, { runLevel: true, tries: 0 });
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      flexWrap="wrap"
      data-testid="endpoint-limits"
      sx={{ pl: 1, pb: 0.5 }}
    >
      <Controller
        control={control}
        name={`${fieldPrefix}.limits.tries`}
        render={({ field }) => (
          <TextField
            size="small"
            type="number"
            label="Tries per row"
            value={field.value ?? ""}
            onChange={(e) => {
              const c = clampTries(e.target.value);
              setRefused(c.refused);
              field.onChange(c.value);
            }}
            inputProps={{
              min: 1,
              max: MAX_TRIES_PER_ROW,
              "data-testid": "endpoint-tries",
            }}
            sx={{ width: 150 }}
            error={Boolean(refused)}
            helperText={
              refused
                ? `At most ${MAX_TRIES_PER_ROW} tries per row (${refused} refused)`
                : tries
                  ? null
                  : "Uses the experiment's Trials"
            }
          />
        )}
      />
      <Controller
        control={control}
        name={`${fieldPrefix}.limits.maxCostUsd`}
        render={({ field }) => (
          <TextField
            size="small"
            type="number"
            label="Cost cap for the run ($)"
            value={field.value ?? ""}
            onChange={(e) => field.onChange(toPositive(e.target.value))}
            inputProps={{ min: 0, step: 0.01, "data-testid": "endpoint-cap" }}
            sx={{ width: 190 }}
          />
        )}
      />
      {capLine ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="endpoint-cap-line"
        >
          {/* W1-6 (owner decision 2, recommended): the run costs $0 in
              Future AGI, so the cap counts its judge calls. */}
          {`Judge spend stops at ${capLine}. Your endpoint bills you directly for its calls.`}
        </Typography>
      ) : null}
    </Stack>
  );
}

EndpointLimitsRow.propTypes = {
  control: PropTypes.object.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
};
