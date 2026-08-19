import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Pane from "../parts/Pane";
import Field from "../parts/Field";
import Tag from "../parts/Tag";
import XCard from "../parts/XCard";
import CodeBlock from "../parts/CodeBlock";
import DataTable from "../parts/DataTable";
import JsonView from "../JsonView";
import { ALK_MONO } from "../alkTokens";

/** The reference's `.subline`: the prose voice inside a card — italic, quiet, one thought. */
const Subline = ({ children }) => (
  <Typography
    variant="body2"
    sx={{ fontStyle: "italic", color: "text.secondary" }}
  >
    {children}
  </Typography>
);

Subline.propTypes = { children: PropTypes.node };

/** A handler earns its keep by being able to refuse; count how many times it does. */
const refusalCount = (source) =>
  (String(source ?? "").match(/raise ToolError/g) || []).length;

const lineCount = (text) => String(text ?? "").split("\n").length;

const EnvironmentTab = ({ world, subgoals }) => {
  const tables = world?.tables ?? [];
  const handlers = world?.handlers ?? [];
  const sequences = world?.sequences ?? [];
  const simulatorPrompt = subgoals?.simulator_prompt ?? "";
  const goals = subgoals?.sub_goals ?? [];

  // The world lives in memory until save_world writes it, so `tables: []` is the ordinary
  // state for most of the build stage — not a failure worth an error surface.
  if (tables.length === 0) {
    return (
      <Box>
        <Pane title="Environment">
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            Not built yet. This stage builds whatever the agent&apos;s tools act
            on — a database, a service, whatever it depends on — so every call
            it makes gets a truthful answer, including a truthful refusal.
          </Typography>
        </Pane>
      </Box>
    );
  }

  const settledByCode = goals.filter(
    (goal) => goal.settled_by === "code",
  ).length;

  return (
    <Box>
      {world?.notes && (
        <Pane title="Builder's notes">
          <Subline>{world.notes}</Subline>
        </Pane>
      )}

      <Pane
        title="The data"
        meta={`${tables.length} tables — click one to see its rows`}
      >
        {tables.map((table) => (
          <XCard
            key={table.name}
            title={table.name}
            meta={`${table.count} rows`}
            // A table small enough to read at a glance is opened for you; anything larger
            // would push the rest of the stage off the screen.
            open={table.count > 0 && table.count <= 4}
          >
            <DataTable
              columns={table.columns ?? []}
              rows={table.rows ?? []}
              count={table.count}
            />
          </XCard>
        ))}
      </Pane>

      {handlers.length > 0 && (
        <Pane
          title="The tools"
          meta="one handler per tool — the code that can say no"
        >
          {handlers.map((handler) => {
            const refusals = refusalCount(handler.source);
            return (
              <XCard
                key={handler.name}
                title={handler.name}
                meta={`${lineCount(handler.source)} lines · ${refusals} refusal${
                  refusals === 1 ? "" : "s"
                }`}
              >
                <CodeBlock>{handler.source}</CodeBlock>
              </XCard>
            );
          })}
        </Pane>
      )}

      {sequences.length > 0 && (
        <Pane
          title="Declared sequences"
          meta="flows where state must carry across calls"
        >
          {sequences.map((sequence) => {
            const calls = sequence.calls ?? [];
            const expectState = sequence.expect_state ?? {};
            return (
              <XCard
                key={sequence.name}
                title={sequence.name}
                meta={`${calls.length} calls`}
              >
                <Field label="calls">
                  <Box
                    component="ol"
                    sx={{
                      m: 0,
                      pl: 2.5,
                      fontFamily: ALK_MONO,
                      fontSize: 11.6,
                      color: "text.primary",
                      "& li": {
                        py: 0.3,
                        borderBottom: "1px solid",
                        borderColor: "divider",
                        "&:last-of-type": { borderBottom: 0 },
                      },
                    }}
                  >
                    {calls.map((step, index) => (
                      // A sequence may call the same tool twice, so position is the only key.
                      // eslint-disable-next-line react/no-array-index-key
                      <li key={index}>
                        <Box component="span">
                          {step.tool}
                          {step.expect === "refusal" ? "  (must refuse)" : ""}
                        </Box>{" "}
                        <Box
                          component="span"
                          sx={{
                            color: "text.secondary",
                            overflowWrap: "anywhere",
                          }}
                        >
                          {JSON.stringify(step.arguments ?? {})}
                        </Box>
                      </li>
                    ))}
                  </Box>
                </Field>

                {Object.keys(expectState).length > 0 && (
                  <Field label="state afterwards must show">
                    <JsonView value={expectState} />
                  </Field>
                )}
              </XCard>
            );
          })}
        </Pane>
      )}

      {/* The simulator prompt and the sub-goal catalogue are this stage's output too — they
          come from /api/subgoals, but they belong to the environment the scenarios run in. */}
      {simulatorPrompt && (
        <Pane
          title="The simulated person"
          meta="written once; each scenario fills its slot"
        >
          <XCard
            title="simulator prompt"
            meta={`${lineCount(simulatorPrompt)} lines`}
          >
            <CodeBlock wrap>{simulatorPrompt}</CodeBlock>
          </XCard>
        </Pane>
      )}

      {goals.length > 0 && (
        <Pane
          title="Sub-goals"
          meta={`${goals.length} shared across every scenario, ${settledByCode} settled by code`}
        >
          {goals.map((goal) => {
            const byCode = goal.settled_by === "code";
            return (
              <XCard
                key={goal.name}
                title={goal.name}
                tags={
                  <Tag kind={byCode ? "code" : "evalHarness"}>
                    {byCode ? "code" : "eval harness"}
                  </Tag>
                }
                meta={(goal.what || "").slice(0, 60)}
              >
                <Field label="what it means">
                  <Subline>{goal.what}</Subline>
                </Field>
                {goal.check && (
                  <Field label="the check, in code">
                    <CodeBlock>{goal.check}</CodeBlock>
                  </Field>
                )}
                {goal.judged && (
                  <Field label="what the eval harness must decide">
                    <Subline>{goal.judged}</Subline>
                  </Field>
                )}
              </XCard>
            );
          })}
        </Pane>
      )}
    </Box>
  );
};

EnvironmentTab.propTypes = {
  world: PropTypes.object,
  subgoals: PropTypes.object,
};

export default EnvironmentTab;
