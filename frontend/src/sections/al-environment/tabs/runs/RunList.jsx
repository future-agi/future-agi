import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "../../alkTokens";
import Pane from "../../parts/Pane";
import Tag from "../../parts/Tag";

/**
 * One simulation over the whole suite is one run, kept as a folder. A session accumulates them
 * over the same scenarios and the same world, so which run a result came from is part of the
 * result: the list is the first thing, and any entry opens into what actually happened.
 */
const RunList = ({ runs, selectedRunId, onSelectRun }) => (
  <Pane
    title="Simulations"
    meta={`${runs.length} run${runs.length === 1 ? "" : "s"} of this suite`}
  >
    <Stack spacing={1.2}>
      {runs.map((run) => {
        // On the list payload `scenarios` is a count. read_run replaces it with the results
        // array, which is why the detail view never reuses this number.
        const total = run.scenarios ?? 0;
        const open = () => onSelectRun(run.run_id);
        return (
          <Box
            key={run.run_id}
            role="button"
            tabIndex={0}
            onClick={open}
            // The whole card opens the run: a lone "open" button meant the obvious gesture,
            // clicking the thing you want, did nothing.
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                open();
              }
            }}
            sx={{
              px: 3.6,
              py: 3,
              borderRadius: "8px",
              cursor: "pointer",
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor:
                selectedRunId === run.run_id ? "text.secondary" : "divider",
              transition: "border-color 120ms, transform 120ms",
              "&:hover": {
                borderColor: "text.secondary",
                transform: "translateY(-1px)",
              },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "accent.pass",
                outlineOffset: "2px",
              },
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={2}
              flexWrap="wrap"
              useFlexGap
            >
              {/* Red is for failure, not for progress: a run where some scenarios passed has not
                  failed, so only a run that passed nothing wears it. */}
              <Tag
                kind={
                  run.passed === total
                    ? "pass"
                    : (run.passed ?? 0) === 0
                      ? "fail"
                      : "soft"
                }
              >
                {`${run.passed ?? 0}/${total} passed`}
              </Tag>
              <Typography
                component="span"
                sx={{
                  fontFamily: ALK_MONO,
                  fontSize: 13.5,
                  fontWeight: 600,
                  color: "text.primary",
                }}
              >
                {run.run_id}
              </Typography>
              <Box sx={{ flex: "1 1 auto" }} />
              <Typography
                component="span"
                variant="caption"
                color="text.secondary"
              >
                {`${run.seconds}s · ${run.modality || "chat"} · $${run.spent_usd || 0}`}
              </Typography>
              <Box
                component="span"
                aria-hidden
                sx={{ color: "text.secondary", fontSize: 14 }}
              >
                ›
              </Box>
            </Stack>

            {/* Which scenarios, at a glance, so the list answers "what changed between runs"
                without opening each one. */}
            {(run.results || []).length > 0 && (
              <Stack
                direction="row"
                spacing={1.4}
                flexWrap="wrap"
                useFlexGap
                sx={{ mt: 1.4 }}
              >
                {run.results.map((one) => (
                  <Tag key={one.scenario} kind={one.passed ? "pass" : "fail"}>
                    {one.scenario}
                  </Tag>
                ))}
              </Stack>
            )}

            {run.models && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 1 }}
              >
                {`agent ${run.models.agent} · user ${run.models.user} · eval harness ${run.models.judge}`}
              </Typography>
            )}
          </Box>
        );
      })}
    </Stack>
  </Pane>
);

RunList.propTypes = {
  runs: PropTypes.array.isRequired,
  selectedRunId: PropTypes.string,
  onSelectRun: PropTypes.func.isRequired,
};

export default RunList;
