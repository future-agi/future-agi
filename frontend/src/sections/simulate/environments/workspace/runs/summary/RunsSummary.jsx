import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box, Stack, Typography, Button, TextField, MenuItem, Checkbox, ListItemText,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { currentEnvVersion } from "src/api/simulate-environments/_fixtures/versions";
import SectionCard from "../../../components/SectionCard";
import { useRunsSummary } from "./useRunsSummary";
import SummaryGraph from "./SummaryGraph";
import SummaryTable from "./SummaryTable";

// The populated Runs tab: every run of the environment as one summary — the
// eval-score trend graph over a comparison table. Replaces the pre-flight card
// once at least one run exists. Comparing/winner/trials are later phases,
// surfaced as "coming soon" so the shell matches the design without faking the
// behaviour.
export default function RunsSummary({ env, envState, onOpenRun, onGo }) {
  const { rows, rowsChrono, evals, series } = useRunsSummary(env, envState);
  const scenarioCount = envState.scenarios?.length ?? 0;

  // Which eval lines to draw. Defaults to all; the last one cannot be unticked
  // (an empty chart reads as a bug, not a choice).
  const [hiddenIds, setHiddenIds] = useState([]);
  const shown = evals.filter((e) => !hiddenIds.includes(e.id));
  const shownSeries = series.filter((s) => shown.some((e) => e.id === s.id));
  const categories = rowsChrono.map((r) => r.label);
  // The env version each run ran against — the same label the VersionBar shows
  // ("v1"), so the row reads "Run 1 · agent v1 × env v1" like the designer.
  const envVersion = currentEnvVersion(env, envState)?.label;

  const toggleEval = (ids) => {
    // ids = the currently-checked set from the multi-select.
    if (!ids.length) return; // keep at least one line
    setHiddenIds(evals.filter((e) => !ids.includes(e.id)).map((e) => e.id));
  };

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ mb: 3 }}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            Simulations summary
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {rows.length} {rows.length === 1 ? "run" : "runs"} · {scenarioCount} scenarios
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          <Button
            variant="outlined" size="small" onClick={() => onGo?.("evals")}
            startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
          >
            Add Evals
          </Button>
          <CustomTooltip show arrow size="small" title="Choosing a winner is coming soon">
            <span>
              <Button
                variant="outlined" size="small" disabled
                startIcon={<Iconify icon="solar:cup-star-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                Choose winner
              </Button>
            </span>
          </CustomTooltip>
        </Stack>
      </Stack>

      <SectionCard>
        {/* eval filter + legend */}
        <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, pt: 1.5, pb: 0.5 }}>
          {evals.length > 0 && (
            <TextField
              select size="small"
              value={shown.map((e) => e.id)}
              onChange={(e) => toggleEval(e.target.value)}
              SelectProps={{
                multiple: true,
                renderValue: (ids) =>
                  ids.length === evals.length
                    ? `All ${evals.length} evals`
                    : evals.filter((x) => ids.includes(x.id)).map((x) => x.name).join(", "),
              }}
              sx={{ width: 200, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2", py: 0.5 } }}
            >
              {evals.map((e) => {
                const on = shown.some((x) => x.id === e.id);
                return (
                  <MenuItem key={e.id} value={e.id} sx={{ typography: "s2", py: 0.5 }}>
                    <Checkbox size="small" checked={on} disabled={on && shown.length === 1} sx={{ p: 0.5, mr: 0.75 }} />
                    <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: e.color, mr: 1, flexShrink: 0 }} />
                    <ListItemText primaryTypographyProps={{ typography: "s2" }} primary={e.name} />
                  </MenuItem>
                );
              })}
            </TextField>
          )}
          <Stack direction="row" spacing={1.5} flexWrap="wrap" rowGap={0.75} sx={{ flex: 1, minWidth: 0, justifyContent: "flex-end" }}>
            {shown.map((e) => (
              <Stack key={e.id} direction="row" alignItems="center" spacing={0.625}>
                <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: e.color, flexShrink: 0 }} />
                <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>{e.name}</Typography>
              </Stack>
            ))}
          </Stack>
        </Stack>

        <SummaryGraph categories={categories} series={shownSeries} />

        <Box sx={{ px: 2.5, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
            Runs ({rows.length})
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Select two or more to compare them scenario by scenario
          </Typography>
        </Box>

        <SummaryTable rows={rows} evals={evals} envVersion={envVersion} onOpenRun={onOpenRun} />
      </SectionCard>
    </Box>
  );
}

RunsSummary.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }).isRequired,
  envState: PropTypes.shape({ scenarios: PropTypes.array }).isRequired,
  onOpenRun: PropTypes.func,
  onGo: PropTypes.func,
};
