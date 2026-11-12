// Config of any two runs (W5-6 B6): Explore's Config tab. Grouped settings,
// run A | run B, `Differences only` on by default with the count; the
// prompt opens a side-by-side line diff. Rows come from configRows.js
// (the one configDiff).
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  FormControlLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { diffLines } from "diff";
import { configRows, differencesText, promptLines } from "./configRows";

const tabular = { fontVariantNumeric: "tabular-nums" };

function RunSelect({ label, value, runs, onChange }) {
  return (
    <Stack direction="row" alignItems="center" gap={0.75}>
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
      <Select
        size="small"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        inputProps={{ "aria-label": `Run ${label}` }}
        sx={{ height: 30, typography: "s2", minWidth: 200 }}
      >
        {runs.map((r) => (
          <MenuItem key={r.run_id} value={r.run_id} sx={{ typography: "s1" }}>
            {[r.label, r.name].filter(Boolean).join(" ")}
          </MenuItem>
        ))}
      </Select>
    </Stack>
  );
}

RunSelect.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  runs: PropTypes.array,
  onChange: PropTypes.func,
};

/** Side-by-side line diff of two prompt templates. */
function PromptDiff({ from, to }) {
  const parts = diffLines(promptLines(from), promptLines(to));
  const side = (keep) =>
    parts
      .filter((p) => (keep === "a" ? !p.added : !p.removed))
      .map((p, i) => (
        <Box
          // eslint-disable-next-line react/no-array-index-key
          key={i}
          component="pre"
          sx={{
            m: 0,
            px: 1,
            whiteSpace: "pre-wrap",
            typography: "s3",
            fontFamily: "monospace",
            bgcolor:
              keep === "a" && p.removed
                ? "error.lighter"
                : keep === "b" && p.added
                  ? "success.lighter"
                  : "transparent",
          }}
        >
          {p.value}
        </Box>
      ));
  return (
    <Box
      data-testid="prompt-line-diff"
      sx={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 1,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 1,
        mt: 0.5,
      }}
    >
      <Box>{side("a")}</Box>
      <Box>{side("b")}</Box>
    </Box>
  );
}

PromptDiff.propTypes = { from: PropTypes.array, to: PropTypes.array };

const ConfigDiffView = ({ runs, defaultA, defaultB }) => {
  const [aId, setA] = useState(defaultA ?? runs[0]?.run_id ?? null);
  const [bId, setB] = useState(
    defaultB ?? runs.find((r) => r.run_id !== aId)?.run_id ?? null,
  );
  const [diffOnly, setDiffOnly] = useState(true);
  const [openPrompt, setOpenPrompt] = useState(false);
  const a = runs.find((r) => r.run_id === aId) ?? null;
  const b = runs.find((r) => r.run_id === bId) ?? null;
  const model = useMemo(
    () =>
      a && b
        ? configRows(a, b, { differencesOnly: diffOnly })
        : { count: 0, groups: [] },
    [a, b, diffOnly],
  );
  return (
    <Stack gap={1.5} data-testid="config-diff-view">
      <Stack direction="row" alignItems="center" gap={2} flexWrap="wrap">
        <RunSelect label="A" value={aId} runs={runs} onChange={setA} />
        <RunSelect label="B" value={bId} runs={runs} onChange={setB} />
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={diffOnly}
              onChange={(e) => setDiffOnly(e.target.checked)}
              inputProps={{ "aria-label": "Differences only" }}
            />
          }
          label={<Typography typography="s2">Differences only</Typography>}
        />
        <Typography
          typography="s1"
          fontWeight={600}
          data-testid="config-diff-count"
          data-count={model.count}
        >
          {differencesText(model.count)}
        </Typography>
      </Stack>
      {!a || !b || !a.config || !b.config ? (
        <Typography typography="s2" color="text.secondary">
          Pick two runs that recorded their settings.
        </Typography>
      ) : !model.groups.length ? (
        <Typography typography="s2" color="text.secondary">
          These two runs used the same settings.
        </Typography>
      ) : (
        <Box
          sx={{
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "background.paper",
          }}
        >
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "180px minmax(0,1fr) minmax(0,1fr)",
              columnGap: 2,
              px: 1.5,
              py: 0.75,
              borderBottom: 1,
              borderColor: "divider",
            }}
          >
            <Typography typography="s2" color="text.secondary">
              Setting
            </Typography>
            <Typography typography="s2" fontWeight={600}>
              {a.label} {a.name}
            </Typography>
            <Typography typography="s2" fontWeight={600}>
              {b.label} {b.name}
            </Typography>
          </Box>
          {model.groups.map((g) => (
            <Box key={g.title} sx={{ px: 1.5, py: 0.75 }}>
              <Typography
                typography="s3"
                color="text.secondary"
                fontWeight={600}
                sx={{ textTransform: "uppercase", letterSpacing: 0.4 }}
              >
                {g.title}
              </Typography>
              {g.rows.map((r) => (
                <Box key={r.key}>
                  <Box
                    data-testid="config-row"
                    data-axis={r.axis}
                    data-changed={r.changed ? "1" : "0"}
                    data-text={r.text}
                    sx={{
                      display: "grid",
                      gridTemplateColumns: "180px minmax(0,1fr) minmax(0,1fr)",
                      columnGap: 2,
                      py: 0.25,
                      alignItems: "baseline",
                    }}
                  >
                    <Typography typography="s2" color="text.secondary">
                      {r.name}
                    </Typography>
                    {r.a == null ? (
                      <Typography
                        typography="s2"
                        fontWeight={600}
                        sx={{ gridColumn: "2 / span 2", ...tabular }}
                      >
                        {r.text}
                      </Typography>
                    ) : (
                      <>
                        <Typography
                          typography="s2"
                          sx={{ overflowWrap: "anywhere", ...tabular }}
                        >
                          {r.a}
                        </Typography>
                        <Typography
                          typography="s2"
                          fontWeight={r.changed ? 600 : 400}
                          sx={{ overflowWrap: "anywhere", ...tabular }}
                        >
                          {r.b}
                          {r.axis === "prompt" &&
                            r.changed &&
                            r.chip?.detail?.messages && (
                              <Button
                                size="small"
                                sx={{ ml: 1, py: 0, minWidth: 0 }}
                                onClick={() => setOpenPrompt((o) => !o)}
                              >
                                {openPrompt ? "Hide line diff" : "Line diff"}
                              </Button>
                            )}
                        </Typography>
                      </>
                    )}
                  </Box>
                  {r.axis === "prompt" &&
                    openPrompt &&
                    r.chip?.detail?.messages && (
                      <PromptDiff
                        from={r.chip.detail.messages.from}
                        to={r.chip.detail.messages.to}
                      />
                    )}
                </Box>
              ))}
            </Box>
          ))}
        </Box>
      )}
    </Stack>
  );
};

ConfigDiffView.propTypes = {
  runs: PropTypes.array.isRequired,
  defaultA: PropTypes.string,
  defaultB: PropTypes.string,
};

export default ConfigDiffView;
