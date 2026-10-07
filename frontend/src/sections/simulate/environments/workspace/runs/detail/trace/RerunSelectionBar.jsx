import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { MAX_CALLS_PER_RUN } from "src/api/simulate-environments/rerunScenarios";

import { toolbarButtonSx } from "./traceTable.constants";

const TRIAL_OPTIONS = Array.from({ length: 20 }, (_, i) => i + 1);
const plural = (n, one, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/**
 * What the run table shows once calls are ticked: how many, Clear, and the
 * Re-run menu. A re-run works per scenario and repeats each one `trials`
 * times, so the menu counts scenarios and shows the calls that will make.
 *
 * `resolveScenarioKeys` returns the scenarios behind the selection. It is read
 * when the menu opens: for "all matching calls" that means reading the calls
 * the table never loaded, so the menu waits for it before offering the run.
 */
export default function RerunSelectionBar({
  selectedCalls,
  scenarioCount = null,
  allMatching = false,
  runTrials = 1,
  disabledReason = null,
  resolveScenarioKeys,
  onRerun,
  onClear,
}) {
  const [anchor, setAnchor] = useState(null);
  const [keys, setKeys] = useState(null);
  const [failed, setFailed] = useState(false);
  const [trials, setTrials] = useState(runTrials);

  const countLabel =
    allMatching || scenarioCount == null
      ? `${plural(selectedCalls, "call")} selected`
      : scenarioCount === selectedCalls
        ? `${selectedCalls.toLocaleString()} selected`
        : `${plural(selectedCalls, "call")} selected · ${plural(scenarioCount, "scenario")}`;

  const openMenu = async (event) => {
    setAnchor(event.currentTarget);
    setTrials(runTrials);
    setKeys(null);
    setFailed(false);
    try {
      setKeys(await resolveScenarioKeys());
    } catch {
      setFailed(true);
    }
  };
  const closeMenu = () => setAnchor(null);

  const scenarios = keys?.length ?? 0;
  const calls = scenarios * trials;
  const overLimit = calls > MAX_CALLS_PER_RUN;
  const ready = keys != null && scenarios > 0;
  const blocked = !ready || overLimit;

  const start = () => {
    if (blocked) return;
    closeMenu();
    onRerun(keys, trials);
  };

  return (
    <Stack direction="row" alignItems="center" spacing={1} useFlexGap>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightSemiBold",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        {countLabel}
      </Typography>
      <Button
        size="small"
        onClick={onClear}
        sx={{ color: "text.subtitle", fontWeight: 600, minWidth: 0 }}
      >
        Clear
      </Button>
      <CustomTooltip
        show={!!disabledReason}
        size="small"
        arrow
        title={disabledReason}
      >
        <span>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!!disabledReason}
            onClick={openMenu}
            aria-haspopup="menu"
            aria-expanded={!!anchor}
            startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
            endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
            sx={{ ...toolbarButtonSx, fontWeight: 700 }}
          >
            {allMatching || scenarioCount == null
              ? "Re-run"
              : `Re-run ${scenarioCount.toLocaleString()}`}
          </Button>
        </span>
      </CustomTooltip>

      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={closeMenu}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: { mt: 0.75, width: 400, maxWidth: "calc(100vw - 32px)" },
          },
        }}
      >
        <Box role="menu" aria-label="Re-run options" sx={{ py: 1 }}>
          <Typography
            sx={{
              px: 2,
              pt: 0.75,
              pb: 1,
              typography: "s3",
              fontWeight: 700,
              color: "text.subtitle",
              textTransform: "uppercase",
              letterSpacing: 0.4,
            }}
          >
            {failed
              ? "Couldn't read the selected scenarios"
              : keys == null
                ? "Counting scenarios…"
                : `Re-run ${plural(scenarios, "scenario")}`}
          </Typography>

          <Box
            role="menuitem"
            tabIndex={0}
            aria-disabled={blocked}
            onClick={start}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                start();
              }
            }}
            sx={{
              display: "flex",
              gap: 1.25,
              px: 2,
              py: 1.25,
              cursor: blocked ? "not-allowed" : "pointer",
              opacity: blocked ? 0.6 : 1,
              "&:hover": blocked ? undefined : { bgcolor: "action.hover" },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
              },
            }}
          >
            <Iconify
              icon="solar:refresh-bold"
              width={16}
              sx={{ mt: "2px", flexShrink: 0 }}
            />
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                Run as a new simulation
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Makes the calls again with the current agent and env, and
                records a new run.
              </Typography>
            </Box>
          </Box>

          {/* Beside the option, not inside it, so changing Trials never starts
              the run. */}
          <Stack spacing={0.75} sx={{ pl: 5.25, pr: 2, pb: 1 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.25}
              useFlexGap
            >
              <TextField
                select
                size="small"
                label="Trials"
                value={trials}
                onChange={(e) => setTrials(Number(e.target.value))}
                SelectProps={{ native: true }}
                sx={{ width: 96 }}
              >
                {TRIAL_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </TextField>
              {ready && (
                <Typography
                  component="output"
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {`${plural(scenarios, "scenario")} × ${trials} = ${plural(calls, "call")}`}
                </Typography>
              )}
            </Stack>
            {ready && overLimit && (
              <Typography sx={{ typography: "s3", color: "warning.main" }}>
                {`That is ${calls.toLocaleString()} calls; a run allows up to ${MAX_CALLS_PER_RUN}. Lower Trials or select fewer scenarios.`}
              </Typography>
            )}
            {keys != null && scenarios === 0 && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                None of the selected calls has a scenario to re-run.
              </Typography>
            )}
          </Stack>
        </Box>
      </Popover>
    </Stack>
  );
}
RerunSelectionBar.propTypes = {
  selectedCalls: PropTypes.number.isRequired,
  scenarioCount: PropTypes.number,
  allMatching: PropTypes.bool,
  runTrials: PropTypes.number,
  disabledReason: PropTypes.string,
  resolveScenarioKeys: PropTypes.func.isRequired,
  onRerun: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
};
