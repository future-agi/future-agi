import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Button,
  Divider,
  Popover,
  Stack,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { MAX_CALLS_PER_RUN } from "src/api/simulate-environments/rerunScenarios";

import TrialsPicker from "../../../scenarios/TrialsPicker";

// The toolbar's height, without its outlined-button colours: a contained
// button keeps its own text colour.
const rerunButtonSx = {
  typography: "s2",
  fontWeight: "fontWeightBold",
  textTransform: "none",
  height: 28,
  px: 1.25,
  minWidth: 0,
  flexShrink: 0,
  whiteSpace: "nowrap",
};
const plural = (n, one, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

// One action in the Re-run menu: an icon, what it does, and a line on how.
function MenuOption({ icon, title, subtitle, blocked, onSelect }) {
  return (
    <Box
      role="menuitem"
      tabIndex={0}
      aria-disabled={blocked}
      onClick={blocked ? undefined : onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          if (!blocked) onSelect();
        }
      }}
      sx={{
        display: "flex",
        gap: 1,
        px: 1.5,
        py: 0.75,
        cursor: blocked ? "not-allowed" : "pointer",
        opacity: blocked ? 0.6 : 1,
        "&:hover": blocked ? undefined : { bgcolor: "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
        },
      }}
    >
      <Iconify icon={icon} width={16} sx={{ mt: "2px", flexShrink: 0 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {subtitle}
        </Typography>
      </Box>
    </Box>
  );
}
MenuOption.propTypes = {
  icon: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string.isRequired,
  blocked: PropTypes.bool.isRequired,
  onSelect: PropTypes.func.isRequired,
};

/**
 * What the run table shows once calls are ticked: how many, Clear, and the
 * Re-run menu. The menu offers either or both of:
 * - a new simulation, which works per scenario and repeats each one `trials`
 *   times, so it counts scenarios and shows the calls that will make;
 * - re-running the evals, which grades the ticked calls again on this run.
 *
 * `resolveSelection` returns `{ callIds, scenarioKeys }` behind the selection.
 * It is read when the menu opens: for "all matching calls" that means reading
 * the calls the table never loaded, so the menu waits for it before offering
 * either action.
 */
export default function RerunSelectionBar({
  selectedCalls,
  scenarioCount = null,
  allMatching = false,
  runTrials = 1,
  disabledReason = null,
  resolveSelection,
  onRerun = null,
  onRerunEvals = null,
  onClear,
}) {
  const [anchor, setAnchor] = useState(null);
  const [resolved, setResolved] = useState(null);
  const [failed, setFailed] = useState(false);
  const [trials, setTrials] = useState(runTrials);
  // Each open reads the selection afresh; only the latest read may land, so a
  // slow earlier one can't replace a newer count.
  const readRef = useRef(0);

  const countLabel =
    allMatching || scenarioCount == null
      ? `${plural(selectedCalls, "call")} selected`
      : scenarioCount === selectedCalls
        ? `${selectedCalls.toLocaleString()} selected`
        : `${plural(selectedCalls, "call")} selected · ${plural(scenarioCount, "scenario")}`;

  const readSelection = async () => {
    setResolved(null);
    setFailed(false);
    readRef.current += 1;
    const read = readRef.current;
    try {
      const result = await resolveSelection();
      if (read === readRef.current) setResolved(result);
    } catch {
      if (read === readRef.current) setFailed(true);
    }
  };
  const openMenu = (event) => {
    setAnchor(event.currentTarget);
    setTrials(runTrials);
    readSelection();
  };
  const closeMenu = () => setAnchor(null);

  const keys = resolved?.scenarioKeys ?? null;
  const callIds = resolved?.callIds ?? [];
  const scenarios = keys?.length ?? 0;
  const calls = scenarios * trials;
  const overLimit = calls > MAX_CALLS_PER_RUN;
  const ready = keys != null && scenarios > 0;

  const start = () => {
    closeMenu();
    onRerun(keys, trials);
  };
  const regrade = () => {
    closeMenu();
    onRerunEvals(callIds);
  };

  return (
    <Stack direction="row" alignItems="center" spacing={1} useFlexGap>
      {/* Sets the selection apart from the table controls beside it. */}
      <Divider orientation="vertical" flexItem sx={{ my: 0.5, mr: 0.5 }} />
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
            sx={rerunButtonSx}
          >
            Re-run
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
            sx: { mt: 0.75, width: 380, maxWidth: "calc(100vw - 32px)" },
          },
        }}
      >
        <Box role="menu" aria-label="Re-run options" sx={{ py: 0.75 }}>
          {failed && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ px: 1.5, pb: 0.5 }}
            >
              <Typography sx={{ typography: "s3", color: "error.main" }}>
                Couldn&apos;t read the selected calls
              </Typography>
              <Button
                size="small"
                onClick={readSelection}
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  minWidth: 0,
                  px: 1,
                  py: 0.25,
                }}
              >
                Try again
              </Button>
            </Stack>
          )}

          {onRerun && (
            <>
              {/* Set first, then act: Repeats sits above the option, outside
                  it, so changing it never starts the run. */}
              <Stack spacing={0.5} sx={{ px: 1.5, pt: 0.25, pb: 0.5 }}>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  useFlexGap
                >
                  {/* The same Repeats picker as the environment header. */}
                  <TrialsPicker
                    trials={trials}
                    onChange={setTrials}
                    scenarioCount={scenarios}
                    size="xs"
                  />
                  <Typography
                    component="output"
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                      whiteSpace: "nowrap",
                      color: ready ? "text.primary" : "text.subtitle",
                    }}
                  >
                    {ready
                      ? `${plural(scenarios, "scenario")} × ${plural(trials, "repeat")} = ${plural(calls, "call")}`
                      : resolved == null && !failed
                        ? "Counting scenarios…"
                        : ""}
                  </Typography>
                </Stack>
                {ready && overLimit && (
                  <Typography sx={{ typography: "s3", color: "warning.main" }}>
                    {`That is ${calls.toLocaleString()} calls; a run allows up to ${MAX_CALLS_PER_RUN}. Lower Repeats or select fewer scenarios.`}
                  </Typography>
                )}
                {keys != null && scenarios === 0 && (
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    None of the selected calls has a scenario to re-run.
                  </Typography>
                )}
              </Stack>
              <MenuOption
                icon="solar:refresh-bold"
                title="Run as a new simulation"
                subtitle="Makes the calls again with the current agent and env, and records a new run."
                blocked={!ready || overLimit}
                onSelect={start}
              />
            </>
          )}

          {onRerun && onRerunEvals && <Divider sx={{ my: 0.5 }} />}

          {onRerunEvals && (
            <MenuOption
              icon="solar:checklist-minimalistic-bold"
              title="Re-run evals"
              subtitle={
                callIds.length
                  ? `Grades the ${plural(callIds.length, "selected call")} again in this run. No new calls.`
                  : "Grades the selected calls again in this run. No new calls."
              }
              blocked={callIds.length === 0}
              onSelect={regrade}
            />
          )}
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
  resolveSelection: PropTypes.func.isRequired,
  onRerun: PropTypes.func,
  onRerunEvals: PropTypes.func,
  onClear: PropTypes.func.isRequired,
};
