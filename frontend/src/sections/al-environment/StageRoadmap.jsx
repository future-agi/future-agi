import PropTypes from "prop-types";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";
import { ALK_STAGES } from "./stages";

/** What each stage has to show for itself, straight from status.have. */
const stageState = (status) => {
  const have = status?.have || {};
  return {
    reception: { done: Boolean(status?.agent), sub: status?.agent || "" },
    understand: {
      done: Boolean(have.contract),
      sub: have.contract ? "written" : "",
    },
    build: {
      done: Boolean(have.world),
      sub: have.world ? `${have.sub_goals || 0} sub-goals` : "",
    },
    scenarios: {
      done: (have.scenarios || 0) > 0,
      sub: have.scenarios ? `${have.scenarios} proved` : "",
    },
    run: {
      done: (have.runs || 0) > 0,
      sub: have.runs ? `${have.runs_passed || 0}/${have.runs} passed` : "",
    },
  };
};

const DOT = 15;

/**
 * success.main is tuned for a dark background; against white it reads as a pale mint and the
 * finished stages stop looking finished. The darker ramp holds its weight in both themes.
 */
const doneGreen = (theme) =>
  theme.palette.mode === "light"
    ? theme.palette.success.dark
    : theme.palette.success.main;

/**
 * Derived from the theme's own foreground rather than from text.secondary, which in light mode
 * is #262626 against a #1A1A1A primary — near enough identical that the hierarchy inverted:
 * blocked stages rendered darker than the current one. A fraction of the foreground keeps the
 * same relative emphasis in both themes.
 */
const shades = (theme) => ({
  ahead: alpha(theme.palette.text.primary, 0.5), // not reached yet
  reachable: alpha(theme.palette.text.primary, 0.75), // openable, not current
  detail: alpha(theme.palette.text.primary, 0.62), // the sub-label under each stage
});

/** Half a rail, drawn from the column edge to the rim of its own dot. */
const rail = (side, lit, theme) => ({
  position: "absolute",
  top: "50%",
  height: "1px",
  bgcolor: lit ? doneGreen(theme) : theme.palette.divider,
  ...(side === "left"
    ? { left: 0, right: "50%", marginRight: `${DOT / 2}px` }
    : { right: 0, left: "50%", marginLeft: `${DOT / 2}px` }),
});

/**
 * The stages are a dependency chain — there is nothing to build a world from without a
 * contract — so the roadmap is one rail that fills in behind what is finished, rather than
 * five unrelated dots. Each step draws its own two half-rails so the line meets the dots
 * exactly and stays continuous across the whole row.
 */
const StageRoadmap = ({ status, onSelectStage }) => {
  // Resolved here rather than passed as sx callbacks: sx only takes a function for the whole
  // object, so a function used as a single property value is silently ignored.
  const theme = useTheme();
  const tone = shades(theme);
  const states = stageState(status);
  const reachable = status?.stages || {};
  const last = ALK_STAGES.length - 1;

  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      sx={{ flexGrow: 1, maxWidth: 660, mx: "auto" }}
    >
      {ALK_STAGES.map((stage, index) => {
        const state = states[stage.key];
        const current = status?.stage === stage.key;
        // An empty string means reachable. Anything else is the server's own reason.
        const blockedBecause = reachable[stage.key] || "";
        const blocked = Boolean(blockedBecause) || !status?.session;

        const stageColor = (() => {
          if (state.done) return doneGreen(theme);
          if (current) return theme.palette.text.primary;
          return blocked ? tone.ahead : tone.reachable;
        })();

        // A rail is lit when the stage behind it is finished.
        const behindDone = index > 0 && states[ALK_STAGES[index - 1].key].done;

        return (
          <Box
            key={stage.key}
            component="button"
            type="button"
            disabled={blocked}
            title={
              blockedBecause || `open the ${stage.label.toLowerCase()} stage`
            }
            onClick={() => onSelectStage(stage.key)}
            sx={{
              flex: "1 1 0",
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 0.25,
              px: 0,
              py: 1,
              border: "none",
              background: "none",
              cursor: blocked ? "default" : "pointer",
              color: stageColor,
              fontFamily: ALK_MONO,
              // No hover wash: the rail already shows where you are, and a grey block behind
              // one step read as a selection that was not there. The pointer is the affordance.
              "&:not(:disabled):hover .alk-dot[data-state='waiting']": {
                borderColor: tone.reachable,
              },
            }}
          >
            <Box
              sx={{
                position: "relative",
                width: "100%",
                height: DOT,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {index > 0 && (
                <Box aria-hidden sx={rail("left", behindDone, theme)} />
              )}
              {index < last && (
                <Box aria-hidden sx={rail("right", state.done, theme)} />
              )}
              <Box
                className="alk-dot"
                aria-hidden
                data-state={state.done || current ? "settled" : "waiting"}
                sx={{
                  position: "relative",
                  width: DOT,
                  height: DOT,
                  borderRadius: "50%",
                  fontSize: 9,
                  lineHeight: `${DOT - 2}px`,
                  textAlign: "center",
                  border: "1px solid",
                  // divider is near-invisible against the page, so an unreached stage uses the
                  // readable helper grey. The ring is what has to be seen at a glance.
                  borderColor:
                    state.done || current ? "currentColor" : tone.ahead,
                  bgcolor:
                    current && !state.done
                      ? "currentColor"
                      : "background.paper",
                  color: "inherit",
                }}
              >
                {state.done ? "✓" : ""}
              </Box>
            </Box>

            <Typography
              variant="caption"
              noWrap
              sx={{
                fontFamily: ALK_MONO,
                fontWeight: current ? 700 : 400,
                color: "inherit",
              }}
            >
              {stage.label}
            </Typography>
            <Typography
              variant="caption"
              noWrap
              sx={{
                fontFamily: ALK_MONO,
                fontSize: 10,
                maxWidth: "100%",
                color: tone.detail,
              }}
            >
              {state.sub || (current ? "in progress" : " ")}
            </Typography>
          </Box>
        );
      })}
    </Stack>
  );
};

StageRoadmap.propTypes = {
  status: PropTypes.object,
  onSelectStage: PropTypes.func.isRequired,
};

export default StageRoadmap;
