import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "../../alkTokens";
import CodeBlock from "../../parts/CodeBlock";
import Field from "../../parts/Field";
import Pane from "../../parts/Pane";
import Tag from "../../parts/Tag";
import XCard from "../../parts/XCard";
import Transcript from "./Transcript";
import { normalizeRun } from "./normalizeRun";

const FILTERS = ["all", "passed", "failed"];
const TONE = { all: "soft", passed: "pass", failed: "fail" };

/** The older per-scenario record kept its calls as flat strings with the outcome in the text. */
const callTone = (call) => {
  if (/-> refused/.test(call)) return { color: "warning.main", mark: "⃠ " };
  if (/-> crashed/.test(call)) return { color: "error.main", mark: "✗ " };
  return { color: "text.primary", mark: "✓ " };
};

const boxOf = (check) => {
  if (check.broken) return { mark: "!", color: "warning.main" };
  return check.passed
    ? { mark: "✓", color: "success.main" }
    : { mark: "✗", color: "error.main" };
};

/**
 * What the Runs tab showed before a simulation was a folder: one card per scenario record read
 * straight out of runs.json. Kept because a session written by an older harness still has them,
 * and a run that cannot be listed as a simulation must not read as no runs at all.
 */
const LegacyRuns = ({ runs }) => {
  const [filter, setFilter] = useState("all");
  const passed = runs.filter((run) => run.passed).length;
  const shown = runs.filter(
    (run) => filter === "all" || (filter === "passed") === Boolean(run.passed),
  );

  return (
    <Box>
      <Pane title="Results" meta={`${passed} of ${runs.length} passed`}>
        <Stack direction="row" spacing={1.4} flexWrap="wrap" useFlexGap>
          {FILTERS.map((key) => (
            <Box
              key={key}
              component="button"
              type="button"
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
              sx={{ p: 0, border: 0, background: "none", cursor: "pointer" }}
            >
              <Tag kind={TONE[key]} dim={filter !== key}>
                {key}
              </Tag>
            </Box>
          ))}
        </Stack>
        {shown.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {`no ${filter} runs`}
          </Typography>
        )}
      </Pane>

      {shown.map((raw) => {
        const run = normalizeRun(raw);
        // A live call and a local run are told apart by what there is to say about them.
        let how = "";
        if (run.live) how = " · live call";
        else if (run.turns) how = ` · ${run.turns} turns`;
        const meta =
          `${run.met}/${run.of} settled by code` +
          (run.judged.length ? ` · ${run.judged.length} by eval harness` : "") +
          how;
        return (
          <XCard
            key={run.scenario}
            title={run.scenario}
            tags={
              <Tag kind={run.passed ? "pass" : "fail"}>
                {run.passed ? "pass" : "fail"}
              </Tag>
            }
            meta={meta}
            // A failure is what somebody opened this page for, and a lone run has nothing to
            // scan past.
            open={!run.passed || shown.length === 1}
          >
            {run.instruction && (
              <Field label="the caller was told">
                <Box
                  component="blockquote"
                  sx={{
                    m: 0,
                    pl: 2,
                    borderLeft: "2px solid",
                    borderColor: "divider",
                    color: "text.secondary",
                    fontSize: 13,
                  }}
                >
                  {run.instruction}
                </Box>
              </Field>
            )}

            <Field label="the sub-goals">
              <Stack spacing={0.6}>
                {run.checks.map((check) => {
                  const mark = boxOf(check);
                  const failed = !check.passed && !check.broken;
                  return (
                    <Stack
                      key={check.name}
                      direction="row"
                      spacing={1.6}
                      alignItems="flex-start"
                    >
                      <Box
                        component="span"
                        aria-hidden
                        sx={{
                          flex: "0 0 auto",
                          width: "1em",
                          color: mark.color,
                        }}
                      >
                        {mark.mark}
                      </Box>
                      {check.kind && (
                        <Box
                          component="span"
                          sx={{
                            flex: "0 0 auto",
                            width: "5.4em",
                            fontFamily: ALK_MONO,
                            fontSize: 11.6,
                            color: "text.secondary",
                          }}
                        >
                          {check.kind}
                        </Box>
                      )}
                      <Box
                        sx={{
                          flex: "1 1 auto",
                          minWidth: 0,
                          overflowWrap: "anywhere",
                        }}
                      >
                        <Box
                          component="span"
                          sx={{ fontSize: 13, color: "text.primary" }}
                        >
                          {check.name}
                        </Box>
                        {/* A failure needs its reason. So does an eval that passed: the point of
                            routing a claim through a named eval is that it can be read back. */}
                        {check.why && (!check.passed || check.by) && (
                          <Typography
                            sx={{
                              fontSize: 12.4,
                              color: failed ? "error.main" : "text.secondary",
                              pt: 0.2,
                            }}
                          >
                            {check.why}
                          </Typography>
                        )}
                        {check.by && (
                          <Typography
                            sx={{
                              fontFamily: ALK_MONO,
                              fontSize: 11.5,
                              color: "text.secondary",
                            }}
                          >
                            {`decided by ${check.by}`}
                          </Typography>
                        )}
                      </Box>
                    </Stack>
                  );
                })}
                {run.judged.map((name) => (
                  <Stack
                    key={name}
                    direction="row"
                    spacing={1.6}
                    alignItems="flex-start"
                  >
                    <Box
                      component="span"
                      aria-hidden
                      sx={{
                        flex: "0 0 auto",
                        width: "1em",
                        color: "warning.main",
                      }}
                    >
                      ?
                    </Box>
                    <Box
                      component="span"
                      sx={{
                        flex: "0 0 auto",
                        width: "5.4em",
                        fontFamily: ALK_MONO,
                        fontSize: 11.6,
                        color: "text.secondary",
                      }}
                    >
                      eval
                    </Box>
                    <Box
                      component="span"
                      sx={{ fontSize: 13, color: "text.primary" }}
                    >
                      {`${name} — decided by the eval harness`}
                    </Box>
                  </Stack>
                ))}
              </Stack>
            </Field>

            {run.problems.map((problem) => (
              <Box
                key={problem}
                sx={{
                  fontFamily: ALK_MONO,
                  fontSize: 11.8,
                  color: "error.main",
                  bgcolor: "action.hover",
                  border: "1px solid",
                  borderColor: "error.main",
                  borderRadius: "3px",
                  px: 2.2,
                  py: 1.4,
                }}
              >
                {problem}
              </Box>
            ))}

            {run.calls.length > 0 && (
              <Field label="what the agent actually did">
                <Stack>
                  {run.calls.map((call) => {
                    const tone = callTone(call);
                    return (
                      <Typography
                        key={call}
                        sx={{
                          fontFamily: ALK_MONO,
                          fontSize: 11.8,
                          color: tone.color,
                          py: 0.5,
                          borderBottom: "1px solid",
                          borderColor: "divider",
                          overflowWrap: "anywhere",
                          "&:last-of-type": { borderBottom: 0 },
                        }}
                      >
                        {`${tone.mark}${call}`}
                      </Typography>
                    );
                  })}
                </Stack>
              </Field>
            )}

            {run.actions && (
              <Field label="what the agent actually did">
                <CodeBlock wrap>{run.actions}</CodeBlock>
              </Field>
            )}

            {run.transcript && (
              <Field label="the conversation">
                <Transcript spoken={run.transcript} />
              </Field>
            )}
          </XCard>
        );
      })}
    </Box>
  );
};

LegacyRuns.propTypes = { runs: PropTypes.array.isRequired };

export default LegacyRuns;
