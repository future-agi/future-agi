import PropTypes from "prop-types";
import { Fragment, isValidElement, useContext, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Chip, Stack, Typography, Collapse, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { validate, scenarioFolder } from "../_mock/contract";
import { proofStatus, INVALIDATING } from "../_mock/proofs";
import { simulatorPolicy } from "../_mock/simulatorPolicy";
import { parseBrief, subGoalText, asSentence } from "../_mock/scenarioBrief";
import {
  isConnectOnly,
  rulesOf,
  ruleOfScenario,
  subGoalsOfScenario,
} from "../_mock/envModel";
import { withSubGoalMark } from "../_mock/scenarioSubGoals";
import { envLabelOf } from "../_mock/envVersionKinds";
import {
  canRunMeaning,
  proofGatesOf,
  stateActionOf,
  STATE_ACTION_LABEL,
  archivedOf,
  countsTowardNoDecision,
  NO_DECISION_NOTE,
} from "../_mock/scenarioStates";
import { EDIT_EFFECT, SCENARIO_BUMPS } from "../_mock/decoupled";
import { subGoalVersionLabel } from "../_mock/subGoalCatalog";
import {
  StateChip,
  ArchiveControl,
  TextAction,
  MarkToggle,
  ProposedBadge,
  PersonalDataTag,
  ReproTag,
} from "../workspace/scenarios/ScenarioStateBits";
import { protectionOf } from "../_mock/mustPassProtection";
import { personalDataDetail, REPLAY_NOTE } from "../_mock/scenarioSources";
import { ScenarioRowContext } from "../workspace/scenarios/scenarioRowContext";
import { clickable, focusRing } from "./clickable";
import { scenarioTitle } from "../_mock/scenarios";
import ScenarioOrgPart from "../workspace/scenarios/ScenarioOrgPart";

/**
 * A scenario, in full.
 *
 * The row alone says what the task is; it does not say whether the task is
 * worth running. Expanding shows the proof — the world was built, a reference
 * solution passed, and every check was made to fail on purpose — plus the brief
 * the person is given and what the run will be graded on.
 *
 * The reference solution is listed but never run against the agent: it exists
 * to show the scenario is passable, not to tell the agent how. A connect-only
 * environment has no world to run one against, so its proof is a reference
 * transcript the judge grades instead.
 *
 * Given its `state`, it also answers the versioning doc's two questions — can
 * it still run, is it still correct — and offers what that state asks for
 * through `onAction(kind, row, arg)`: reprove · rewrite · archive (arg = note)
 * · restore · update (row = the re-marked row) · subgoal (arg = catalogue id)
 * · edit · approve (add a proposed scenario).
 * `bare` drops the collapsible row, for the detail drawer.
 */
export default function ScenarioDetail({
  row,
  env,
  envState,
  defaultOpen = false,
  buildMode = false,
  state = null,
  onAction,
  locked = false,
  bare = false,
}) {
  const [open, setOpen] = useState(defaultOpen || bare);
  const [showInternals, setShowInternals] = useState(false);
  const { usageOf } = useContext(ScenarioRowContext) || {};
  const s = validate(row, env);
  /* A proof is only true of the world it was run against. In buildMode the
     environment was just derived (v1 by definition), so nothing has drifted
     and every scenario reads as validated. */
  const proof = buildMode
    ? { proved: "v1", current: "v1", stale: false, reasons: [], since: [] }
    : proofStatus(row, env, envState);
  const policy = simulatorPolicy(row, env);
  if (!s) return null;
  const brief = parseBrief(s.task);
  /* Sub-goals arrive as objects in the mocks and as plain strings from the
     API — reading only `.label` left production rows with empty numbers. */
  /* On the scenario, each critical or supporting, each pointing at its shared
     definition. Older rows carry `subTasks` as plain text; they read the same. */
  const goals = subGoalsOfScenario(
    row.subGoals?.length || !row.subTasks?.length
      ? row
      : { ...row, subGoals: row.subTasks.map(subGoalText) },
    env,
    envState,
  );
  const path = conversationPath(row);
  const connect = isConnectOnly(env);
  const gates = proofGatesOf(row, env, {
    stale: proof.stale,
    broken: state?.proof?.id === "broken",
  });
  const rule = state ? ruleOfScenario(row, rulesOf(env, envState)) : null;
  const archived = state?.id === "archived" || !!archivedOf(row);
  const vl = (l) => envLabelOf(env, envState, l);
  const onMark =
    onAction && !locked && !archived
      ? (id, mark) => onAction("update", withSubGoalMark(row, id, mark))
      : null;

  /* The drawer reads the same scenario as a sheet: figures, then ruled parts. */
  if (bare) {
    return (
      <DrawerSheet
        locked={locked}
        row={row}
        env={env}
        envState={envState}
        state={state}
        s={s}
        brief={brief}
        goals={goals}
        path={path}
        connect={connect}
        gates={gates}
        proof={proof}
        policy={policy}
        rule={rule}
        vl={vl}
        usageOf={usageOf}
        onMark={onMark}
        onAction={onAction}
      />
    );
  }

  return (
    <Box>
      {/*
        Collapsed row shape asked for by the product team:
          - Short kebab-case name in mono (row label)
          - One-line summary next to it (context)
          - No VALIDATED chip (moved into the expanded body where the
            proof detail lives, so the row itself stays scannable)
      */}
      {!bare && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          {...clickable(() => setOpen((o) => !o), { expanded: open })}
          sx={{
            ...focusRing,
            px: 2.5,
            py: 1.25,
            cursor: "pointer",
            "&:hover": { bgcolor: "action.hover" },
            /* Archived rows stay listed, dimmed — the chip keeps full strength. */
            ...(archived && { "& .sd-dim": { opacity: 0.45 } }),
          }}
        >
          <Iconify
            icon={
              open
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
            width={14}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
          <Typography
            noWrap
            className="sd-dim"
            sx={{
              typography: "s2",
              fontWeight: 600,
              fontFamily: "ui-monospace, Menlo, monospace",
              color: "text.primary",
              flexShrink: 0,
            }}
          >
            {scenarioTitle(s.title) || s.name}
          </Typography>
          {/* The one-line summary, else the conversation category. Never the
            brief itself — shouted in capitals, it read as an error. */}
          {(s.summary || s.branchCategory) && (
            <Typography
              noWrap
              className="sd-dim"
              sx={{
                typography: "s3",
                color: "text.subtitle",
                flex: 1,
                minWidth: 0,
              }}
            >
              {s.summary || s.branchCategory}
            </Typography>
          )}
          {/* Where it came from is in the scenario's drawer, not on every row. */}
          {row.proposed && <ProposedBadge />}
          {state && <StateChip state={state} />}
        </Stack>
      )}

      {/*
        Expanded: one column of labelled rows. Labels sit in a fixed gutter so
        every fact lines up whether the brief is one line or a page, and the
        content shares one size and colour so nothing competes. The machinery
        — caller policy, reference solution, files — opens from the Proof row.
      */}
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pl: "45px", pr: 2.5, pt: 0.25, pb: 2 }}>
          <Box
            sx={{
              display: "grid",
              /* Wide enough for "Is it still correct?" on one line. */
              gridTemplateColumns: {
                xs: "1fr",
                sm: `${GUTTER}px minmax(0, 1fr)`,
              },
              columnGap: 2.5,
              rowGap: { xs: 0.5, sm: 1.5 },
              "& .sd-val": VAL_SX,
            }}
          >
            {s.expected && (
              <Field label="Passes when">
                <Typography
                  className="sd-val"
                  sx={{ color: "text.primary !important" }}
                >
                  {asSentence(s.expected)}
                </Typography>
              </Field>
            )}

            {s.persona && (
              <Field label="Caller">
                <Typography className="sd-val">
                  <Box
                    component="span"
                    sx={{ color: "text.primary", fontWeight: 600 }}
                  >
                    {s.persona.name}
                  </Box>
                  {[personaMeta(s.persona), s.persona.traits?.join(", ")]
                    .filter(Boolean)
                    .map((part) => ` · ${part}`)}
                </Typography>
              </Field>
            )}

            {hasBrief(brief) && (
              <Field label="Brief">
                <BriefBlock brief={brief} />
              </Field>
            )}

            {goals.length > 0 && (
              <Field label="Sub-goals">
                <SubGoalList
                  goals={goals}
                  usageOf={usageOf}
                  versionOf={(id) => subGoalVersionLabel(envState, id)}
                  /* Re-marking changes what a pass means — the row can't be
                     graded until it is re-proved. */
                  onMark={onMark}
                  onOpenShared={
                    onAction ? (id) => onAction("subgoal", row, id) : null
                  }
                />
                {state?.gradeNote && (
                  <Typography
                    sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
                  >
                    {`${state.gradeNote}.`}
                  </Typography>
                )}
              </Field>
            )}

            {path && (
              <Field label="Path">
                {Array.isArray(path.steps) ? (
                  <Typography
                    className="sd-val"
                    sx={{
                      fontFamily: "ui-monospace, Menlo, monospace",
                      fontSize: "12px !important",
                      wordBreak: "break-word",
                    }}
                  >
                    {path.steps.join("  →  ")}
                  </Typography>
                ) : (
                  <Typography className="sd-val">{path.steps}</Typography>
                )}
              </Field>
            )}

            {s.checks?.length > 0 && (
              <Field label="Graded on">
                <GradedOn checks={s.checks} />
              </Field>
            )}

            {/* Where a production-derived or generated-from-failure scenario
                came from: its group, its reproduce state, the personal data
                replaced, the turns a replay says word for word. */}
            {hasOrigin(row) && (
              <Field label="From">
                <OriginRow row={row} />
              </Field>
            )}

            {/* Every edit, with what it did to past results. */}
            <Field label="Changes">
              <ChangeLog row={row} />
            </Field>

            {/* A proposed scenario waits for someone to add it. */}
            {row.proposed && (
              <Field label="Proposed">
                <ProposedRow
                  row={row}
                  onAction={onAction}
                  locked={locked || archived}
                />
              </Field>
            )}

            {/* The doc's two questions, then what this state asks for. */}
            {state && (
              <>
                <Field label="Can it still run?">
                  <Answer {...canRunAnswer(state, env)} />
                </Field>
                <Field label="Is it still correct?">
                  <Answer {...correctAnswer(state, rule)} />
                </Field>
                <Field label="State">
                  <StateActions
                    state={state}
                    row={row}
                    onAction={onAction}
                    locked={locked}
                  />
                  {/* Stale scenarios still run, but decide nothing (RSI PRD). */}
                  {countsTowardNoDecision(state) && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.subtitle",
                        mt: 0.5,
                        lineHeight: 1.6,
                      }}
                    >
                      {NO_DECISION_NOTE}
                    </Typography>
                  )}
                </Field>
              </>
            )}

            <Field label="Proof">
              <ProofRow
                proof={proof}
                vl={vl}
                gates={gates}
                connect={connect}
                open={showInternals}
                onToggle={() => setShowInternals((o) => !o)}
              />
            </Field>
          </Box>

          <Collapse in={showInternals} unmountOnExit>
            <Internals
              s={s}
              env={env}
              policy={policy}
              connect={connect}
              brief={brief}
              gates={gates}
              proof={proof}
              vl={vl}
            />
          </Collapse>
        </Box>
      </Collapse>
    </Box>
  );
}

ScenarioDetail.propTypes = {
  envState: PropTypes.object,
  row: PropTypes.object,
  env: PropTypes.object,
  defaultOpen: PropTypes.bool,
  buildMode: PropTypes.bool,
  state: PropTypes.object,
  onAction: PropTypes.func,
  locked: PropTypes.bool,
  bare: PropTypes.bool,
};

const GUTTER = 128;

export function ValidatedBadge({ stale }) {
  return (
    <Chip
      size="small"
      color={stale ? "warning" : "success"}
      label={stale ? "RE-PROVE" : "VALIDATED"}
      sx={{ flexShrink: 0 }}
    />
  );
}
ValidatedBadge.propTypes = { stale: PropTypes.bool };

export function CheckChip({ check }) {
  const judged = check.kind === "judge";
  return (
    <Chip
      size="small"
      color={judged ? "warning" : "success"}
      label={
        <>
          {check.label}
          <Box component="span" sx={{ ml: 0.5, color: "text.tertiary" }}>
            {judged ? "judged" : "world state"}
          </Box>
        </>
      }
    />
  );
}
CheckChip.propTypes = { check: PropTypes.object };

function PolicyRow({ label, children }) {
  /*
    Two-column layout — label on the left, value on the right. Reads as
    a real key/value list rather than a stack of uppercase headings +
    prose. Uppercase small-caps here competed with the section headers
    and made the caller-policy block dense.
  */
  return (
    /* Same gutter as the rows above it, so the details open in line. */
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 0.5, sm: 2.5 }}
      sx={{ py: 0.375 }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: { sm: GUTTER },
          flexShrink: 0,
          lineHeight: 1.6,
        }}
      >
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {/* Plain text — one string or several pieces joined in JSX — gets the
            body style; without it "Manner" rendered at the page's base size. */}
        {isValidElement(children) ? (
          children
        ) : (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}
          >
            {children}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}

PolicyRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

function Section({ title, children }) {
  /*
    Softer section headers — sentence-case, medium weight, no small-caps
    letter spacing. The aggressive uppercase treatment competed with the
    scenario title above and made every section header shout equally, so
    the reader had no hierarchy to land on. This lets the body of each
    section carry the eye instead.
  */
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.subtitle",
          mb: 0.75,
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

/* ── pieces ─────────────────────────────────────────────────────────────── */

/* One labelled row: quiet label in the gutter, content beside it. */
function Field({ label, children }) {
  return (
    <>
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          lineHeight: 1.6,
          pt: { sm: 0 },
        }}
      >
        {label}
      </Typography>
      <Box minWidth={0} sx={{ mb: { xs: 1, sm: 0 } }}>
        {children}
      </Box>
    </>
  );
}
Field.propTypes = { label: PropTypes.string, children: PropTypes.node };

const VAL_SX = {
  typography: "s2",
  color: "text.secondary",
  lineHeight: 1.6,
  maxWidth: 760,
};
const MONO = "ui-monospace, Menlo, monospace";
const hasBrief = (brief) =>
  !!(brief.intro || brief.steps.length || brief.details.length);

/* The brief: its opening, its steps, and any key details. */
function BriefBlock({ brief }) {
  return (
    <>
      {brief.intro && <Typography className="sd-val">{brief.intro}</Typography>}
      {brief.steps.length > 0 && (
        <NumberedList items={brief.steps} sx={{ mt: brief.intro ? 0.75 : 0 }} />
      )}
      {brief.details.length > 0 && (
        <Box
          sx={{
            mt: 1,
            display: "grid",
            gridTemplateColumns: "max-content minmax(0, 1fr)",
            columnGap: 2,
            rowGap: 0.25,
            maxWidth: 760,
          }}
        >
          {brief.details.map((d, i) => (
            <Fragment key={`${d.key}-${i}`}>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.subtitle",
                  lineHeight: 1.6,
                }}
              >
                {d.key || "Detail"}
              </Typography>
              <Typography className="sd-val">{d.value}</Typography>
            </Fragment>
          ))}
        </Box>
      )}
    </>
  );
}
BriefBlock.propTypes = { brief: PropTypes.object };

/* What a run is graded on — judged or checked against the world, on hover. */
function GradedOn({ checks }) {
  return (
    <Typography className="sd-val">
      {checks.map((c, i) => (
        <Fragment key={c.id}>
          {i > 0 && (
            <Box component="span" sx={{ color: "text.disabled" }}>
              {"  ·  "}
            </Box>
          )}
          <Tooltip
            arrow
            title={
              c.kind === "judge"
                ? "Judged by a grader reading the conversation"
                : "Checked against the world's state after the run"
            }
          >
            <Box
              component="span"
              sx={{
                borderBottom: "1px dotted",
                borderColor: "text.disabled",
                cursor: "help",
              }}
            >
              {checkName(c.label)}
            </Box>
          </Tooltip>
        </Fragment>
      ))}
    </Typography>
  );
}
GradedOn.propTypes = { checks: PropTypes.array };

/* ── the drawer's sheet ─────────────────────────────────────────────────── */

const SECTION_HEAD = {
  typography: "s3",
  fontWeight: 600,
  color: "text.subtitle",
  textTransform: "none",
  letterSpacing: 0.5,
  mb: 1,
};

/*
  One scenario in the drawer, read like a record: four figures across the
  top, then the scenario, its status and — when there is any — its history,
  each a ruled sheet. The actions are in the drawer's header; colour marks
  only a pass, a failure or a check that broke.
*/
function DrawerSheet({
  row,
  env,
  envState,
  state,
  s,
  brief,
  goals,
  path,
  connect,
  gates,
  proof,
  policy,
  rule,
  vl,
  usageOf,
  onMark,
  onAction,
  locked = false,
}) {
  const navigate = useNavigate();
  const [details, setDetails] = useState(false);
  const { toolsOf, answers, historyOf, latestOf } =
    useContext(ScenarioRowContext) || {};
  const tools = toolsOf ? toolsOf(row) : [];
  const history = historyOf ? historyOf(row) || [] : [];
  const latest = latestOf ? latestOf(row) : null;

  const scored = history.filter((h) => RUN_COLOR[h.status]);
  const passedRuns = scored.filter((h) => h.status === "passed").length;
  const critical = goals.filter((g) => g.mark === "critical").length;
  const applicable = gates.filter((g) => !g.na);
  const checksPassed = applicable.filter((g) => g.ok === true).length;
  const checksFailed = applicable.filter((g) => g.ok === false).length;
  const log = row.versionLog || [];

  /* Healthy — it runs and is still correct: one line; the two questions are
     spelled out only when an answer isn't yes. */
  const healthy =
    !!state &&
    state.canRun === true &&
    state.correct === true &&
    state.id !== "archived" &&
    state.id !== "stale";
  const archivedNote = archivedOf(row);
  /* What the state means — not what to do; the header's button says that. */
  const stateLine =
    !state || healthy
      ? null
      : state.id === "archived" && archivedNote
        ? `Archived${archivedNote.at ? ` ${shortDate(archivedNote.at)}` : ""}${archivedNote.note ? ` — ${archivedNote.note}` : ""}`
        : stateActionOf(state) || !state.note
          ? null
          : `${state.note.charAt(0).toUpperCase()}${state.note.slice(1)}`;
  const showHistory = log.length > 0 || row.proposed || hasOrigin(row);
  const openLatest = latest?.runId
    ? () =>
        navigate(
          `${paths.dashboard.simulate.simulationRun(env.id, latest.runId)}?task=${encodeURIComponent(latest.task.scenarioId || latest.task.id)}`,
        )
    : null;

  return (
    <Stack spacing={2.5} sx={{ px: 2.5, py: 2.25, "& .sd-val": VAL_SX }}>
      {/* figures */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          "& > * + *": { borderLeft: "1px solid", borderColor: "divider" },
        }}
      >
        <Figure
          label={history.length ? `Last ${history.length} runs` : "Runs"}
          value={scored.length ? `${passedRuns}/${scored.length}` : "—"}
          sub={history.length ? <RunDots history={history} /> : "Not run yet"}
        />
        <Figure
          label="Sub-goals"
          value={goals.length || "—"}
          sub={goals.length ? `${critical} critical` : "None set"}
        />
        <Figure
          label="Checks"
          value={
            applicable.length ? `${checksPassed}/${applicable.length}` : "—"
          }
          sub={
            checksFailed ? (
              <Box
                component="span"
                sx={{ color: "status.danger.fg", fontWeight: 600 }}
              >{`${checksFailed} failed`}</Box>
            ) : proof.stale ? (
              `Not yet proved on ${vl(proof.current)}`
            ) : (
              `Passed on ${vl(proof.proved)}`
            )
          }
        />
        <Figure
          label="Changes"
          value={log.length ? String(log.length) : "None"}
          sub={
            row.proposed
              ? "Proposed"
              : log.length
                ? "Edits in History"
                : "As written"
          }
        />
      </Box>

      {/* the scenario */}
      <Part title="Scenario">
        {s.expected && (
          <Row label="Passes when">
            <Typography
              className="sd-val"
              sx={{ color: "text.primary !important", fontWeight: 500 }}
            >
              {asSentence(s.expected)}
            </Typography>
          </Row>
        )}
        {s.persona && (
          <Row label="Caller">
            <Stack direction="row" alignItems="center" spacing={1}>
              <Box
                sx={{
                  /* The line's height, so the caller lines up with its label. */
                  width: 21,
                  height: 21,
                  borderRadius: "50%",
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: "action.hover",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  sx={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "text.secondary",
                    letterSpacing: 0.2,
                  }}
                >
                  {initialsOf(s.persona.name)}
                </Typography>
              </Box>
              <Typography className="sd-val">
                <Box
                  component="span"
                  sx={{ color: "text.primary", fontWeight: 600 }}
                >
                  {s.persona.name}
                </Box>
                {[personaMeta(s.persona), s.persona.traits?.join(", ")]
                  .filter(Boolean)
                  .map((part) => ` · ${part}`)}
              </Typography>
            </Stack>
          </Row>
        )}
        {hasBrief(brief) && (
          <Row label="Brief">
            <BriefBlock brief={brief} />
          </Row>
        )}
        {goals.length > 0 && (
          <Row label="Sub-goals">
            <SubGoalList
              goals={goals}
              usageOf={usageOf}
              versionOf={(id) => subGoalVersionLabel(envState, id)}
              onMark={onMark}
              onOpenShared={
                onAction ? (id) => onAction("subgoal", row, id) : null
              }
            />
            {state?.gradeNote && (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
              >{`${state.gradeNote}.`}</Typography>
            )}
          </Row>
        )}
        {path && (
          <Row label="Path">
            {Array.isArray(path.steps) ? (
              <PathSteps steps={path.steps} />
            ) : (
              <Typography className="sd-val">{path.steps}</Typography>
            )}
          </Row>
        )}
        {s.checks?.length > 0 && (
          <Row label="Graded on">
            <GradedOn checks={s.checks} />
          </Row>
        )}
        {tools.length > 0 && (
          <Row label="Tools it needs">
            <ToolChips tools={tools} answers={answers} />
          </Row>
        )}
      </Part>

      {/* tags, groups, grounding and the lifecycle — in the drawer */}
      {env?.id && <ScenarioOrgPart env={env} row={row} locked={locked} />}

      {/* status */}
      <Part title="Status">
        {state &&
          (healthy ? (
            <Row label="Health">
              <HealthLine
                canRun={canRunAnswer(state, env).text}
                correct={correctAnswer(state, rule)}
              />
            </Row>
          ) : (
            <>
              <Row label="Can it still run?">
                <Answer {...canRunAnswer(state, env)} />
              </Row>
              <Row label="Is it still correct?">
                <Answer {...correctAnswer(state, rule)} />
              </Row>
            </>
          ))}
        {state && (stateLine || countsTowardNoDecision(state)) && (
          <Row label="State">
            {stateLine && (
              <Typography className="sd-val">{stateLine}</Typography>
            )}
            {countsTowardNoDecision(state) && (
              <Typography className="sd-val">{NO_DECISION_NOTE}</Typography>
            )}
          </Row>
        )}
        {/* What keeps an improvement from breaking it — the gate's own three-part set. */}
        {(() => {
          const p = protectionOf(row, latest);
          return (
            <Row label="Protection">
              <Typography className="sd-val">
                <Box
                  component="span"
                  sx={{ color: "text.primary", fontWeight: 600 }}
                >
                  {p.label}
                </Box>
                {` — ${p.line.charAt(0).toLowerCase()}${p.line.slice(1)}`}
              </Typography>
            </Row>
          );
        })()}
        {latest?.runId && (
          <Row label="Latest run">
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography className="sd-val">
                <Box
                  component="span"
                  sx={{ color: "text.primary", fontWeight: 600 }}
                >
                  {latest.name}
                </Box>
                {" · "}
                <Box
                  component="span"
                  sx={{
                    color:
                      !latest.task.pulled && latest.task.status === "failed"
                        ? "status.danger.fg"
                        : "inherit",
                    fontWeight:
                      !latest.task.pulled && latest.task.status === "failed"
                        ? 600
                        : 400,
                  }}
                >
                  {latest.task.pulled
                    ? "Not scored"
                    : RUN_WORD[latest.task.status] || "Not scored"}
                </Box>
              </Typography>
              <Box sx={{ flex: 1 }} />
              <InlineLink onClick={openLatest}>
                Open conversation
                <Iconify icon="solar:arrow-right-linear" width={12} />
              </InlineLink>
            </Stack>
          </Row>
        )}
        <Row label="Proof">
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            flexWrap="wrap"
            rowGap={0.25}
          >
            {gates
              .filter((g) => g.core)
              .map((g, i) => (
                <Fragment key={g.id}>
                  {i > 0 && (
                    <Box
                      component="span"
                      sx={{ color: "text.disabled", typography: "s2" }}
                    >
                      ·
                    </Box>
                  )}
                  <GateMark
                    gate={g}
                    tip={
                      g.na
                        ? g.detail
                        : g.ok === false && g.reason
                          ? g.reason
                          : g.detail
                    }
                  />
                </Fragment>
              ))}
            <Box sx={{ flex: 1 }} />
            <InlineLink onClick={() => setDetails((o) => !o)}>
              {details ? "Hide details" : "Details"}
              <Iconify
                icon={
                  details
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={12}
              />
            </InlineLink>
          </Stack>
          {proof.stale && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
            >{`${proof.reasons.map((r) => INVALIDATING[r]).join("; ")}.`}</Typography>
          )}
        </Row>
        {details && (
          <Box className="sr" sx={{ px: 1.75, py: 1.5 }}>
            <GateList gates={gates} vl={vl} proof={proof} />
            <Internals
              s={s}
              env={env}
              policy={policy}
              connect={connect}
              brief={brief}
              gates={gates}
              proof={proof}
              vl={vl}
            />
          </Box>
        )}
      </Part>

      {/* history — only when there is some */}
      {showHistory && (
        <Part title="History">
          {hasOrigin(row) && (
            <Row label="From">
              <OriginRow row={row} />
            </Row>
          )}
          {log.length > 0 && (
            <Row label="Changes">
              <ChangeLog row={row} />
            </Row>
          )}
          {row.proposed && (
            <Row label="Proposed">
              <ProposedRow row={row} onAction={null} locked />
            </Row>
          )}
        </Part>
      )}
    </Stack>
  );
}
DrawerSheet.propTypes = {
  locked: PropTypes.bool,
  row: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  state: PropTypes.object,
  s: PropTypes.object,
  brief: PropTypes.object,
  goals: PropTypes.array,
  path: PropTypes.object,
  connect: PropTypes.bool,
  gates: PropTypes.array,
  proof: PropTypes.object,
  policy: PropTypes.object,
  rule: PropTypes.object,
  vl: PropTypes.func,
  usageOf: PropTypes.func,
  onMark: PropTypes.func,
  onAction: PropTypes.func,
};

/* One figure across the top: what it counts, the count, and a quiet line under it. */
function Figure({ label, value, sub }) {
  return (
    <Box sx={{ px: 1.75, py: 1.25, minWidth: 0 }}>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      <Typography
        noWrap
        sx={{
          typography: "m3",
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          lineHeight: 1.35,
          mt: 0.25,
        }}
      >
        {value}
      </Typography>
      <Box
        sx={{ mt: 0.375, minHeight: 18, display: "flex", alignItems: "center" }}
      >
        {typeof sub === "string" ? (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {sub}
          </Typography>
        ) : (
          <Typography
            component="div"
            noWrap
            sx={{ typography: "s3", color: "text.subtitle" }}
          >
            {sub}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
Figure.propTypes = {
  label: PropTypes.node,
  value: PropTypes.node,
  sub: PropTypes.node,
};

/* A titled, ruled sheet. */
function Part({ title, children }) {
  return (
    <Box>
      <Typography sx={SECTION_HEAD}>{title}</Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          "& > .sr + .sr": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
Part.propTypes = { title: PropTypes.string, children: PropTypes.node };

/* One ruled row of a sheet: the label in a fixed gutter, the value beside it. */
function Row({ label, children }) {
  return (
    <Box
      className="sr"
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "124px minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: 0.25,
        px: 1.75,
        py: 1.125,
      }}
    >
      <Typography
        sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
      >
        {label}
      </Typography>
      <Box minWidth={0}>{children}</Box>
    </Box>
  );
}
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };

/* The run history as small dots, oldest first — the run's name and result on hover. */
function RunDots({ history }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      {history.map((h) => (
        <Tooltip
          key={h.runId}
          arrow
          title={`${h.name} · ${RUN_WORD[h.status] || (h.status ? "Not scored" : "Not in this run")}`}
        >
          <Box
            sx={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              flexShrink: 0,
              bgcolor: RUN_COLOR[h.status] || "transparent",
              border: RUN_COLOR[h.status] ? "none" : "1px solid",
              borderColor: "text.disabled",
            }}
          />
        </Tooltip>
      ))}
    </Stack>
  );
}
RunDots.propTypes = { history: PropTypes.array };

/* The conversation's path as its steps, joined. */
function PathSteps({ steps }) {
  return (
    <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.5}>
      {steps.map((st, i) => (
        <Fragment key={`${st}-${i}`}>
          {i > 0 && (
            <Iconify
              icon="solar:arrow-right-linear"
              width={12}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
          )}
          <Chip
            size="small"
            variant="outlined"
            label={st}
            sx={{ fontFamily: MONO }}
          />
        </Fragment>
      ))}
    </Stack>
  );
}
PathSteps.propTypes = { steps: PropTypes.array };

const initialsOf = (name = "") =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");

/* The tools the world must answer — one it can't is outlined red. */
function ToolChips({ tools, answers }) {
  return (
    <Stack direction="row" flexWrap="wrap" gap={0.5}>
      {tools.map((t) => {
        const unanswered = answers && !answers.has(t);
        return (
          <Tooltip
            key={t}
            arrow
            title={
              unanswered
                ? "This environment can't answer it yet — the scenario comes back not measured"
                : ""
            }
          >
            <Chip
              size="small"
              color={unanswered ? "error" : "default"}
              label={t}
              sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
            />
          </Tooltip>
        );
      })}
    </Stack>
  );
}
ToolChips.propTypes = { tools: PropTypes.array, answers: PropTypes.object };

const RUN_COLOR = {
  passed: "status.success.solid",
  failed: "status.danger.solid",
  flaky: "status.warning.solid",
};
const RUN_WORD = { passed: "Passed", failed: "Failed", flaky: "Flaky" };

/* A plain numbered list — the numbers order it, they don't need a badge. */
function NumberedList({ items, sx }) {
  return (
    <Stack spacing={0.5} sx={{ maxWidth: 760, ...sx }}>
      {items.map((text, i) => (
        <Stack key={i} direction="row" spacing={1} alignItems="flex-start">
          <Typography
            sx={{
              typography: "s2",
              color: "text.disabled",
              lineHeight: 1.6,
              width: 14,
              flexShrink: 0,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {i + 1}.
          </Typography>
          <Typography className="sd-val">{text}</Typography>
        </Stack>
      ))}
    </Stack>
  );
}
NumberedList.propTypes = { items: PropTypes.array, sx: PropTypes.object };

/*
  The scenario's sub-goals: what each checks, whether it is critical, and the
  shared definition it points at — editing that re-grades every scenario
  using it, so the count is shown.
*/
function SubGoalList({ goals, usageOf, versionOf, onMark, onOpenShared }) {
  return (
    <Stack spacing={1} sx={{ maxWidth: 760 }}>
      {goals.map((g, i) => {
        const used = usageOf?.(g.id)?.usedBy || 0;
        return (
          <Stack
            key={g.key}
            direction="row"
            spacing={1}
            alignItems="flex-start"
          >
            <Typography
              sx={{
                typography: "s2",
                color: "text.disabled",
                lineHeight: 1.6,
                width: 14,
                flexShrink: 0,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {i + 1}.
            </Typography>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography className="sd-val">{g.text}</Typography>
              {(versionOf || used > 1) && (
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  {versionOf && (
                    <Tooltip
                      arrow
                      title="The shared definition's version — a fix or a policy change publishes the next one; it is never edited in place"
                    >
                      <Typography
                        sx={{
                          typography: "s3",
                          fontFamily: "ui-monospace, Menlo, monospace",
                          fontSize: 12,
                          color: "text.subtitle",
                        }}
                      >
                        {versionOf(g.id).replace(/^.*@/, "@")}
                      </Typography>
                    </Tooltip>
                  )}
                  {used > 1 && (
                    <Tooltip
                      arrow
                      title={`${g.id} — editing the definition re-grades every scenario that uses it`}
                    >
                      <Typography
                        {...clickable(
                          onOpenShared
                            ? (e) => {
                                e.stopPropagation();
                                onOpenShared(g.id);
                              }
                            : undefined,
                          { role: "link" },
                        )}
                        sx={{
                          ...focusRing,
                          typography: "s3",
                          color: "text.subtitle",
                          whiteSpace: "nowrap",
                          ...(onOpenShared && {
                            cursor: "pointer",
                            "&:hover": {
                              color: "text.primary",
                              textDecoration: "underline",
                            },
                          }),
                        }}
                      >
                        {`Shared · used by ${used}`}
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
              )}
            </Box>
            <Box sx={{ pt: "1px" }}>
              <MarkToggle
                value={g.mark}
                onChange={onMark ? (m) => onMark(g.id, m) : null}
                disabled={!onMark}
              />
            </Box>
          </Stack>
        );
      })}
    </Stack>
  );
}
SubGoalList.propTypes = {
  goals: PropTypes.array,
  usageOf: PropTypes.func,
  versionOf: PropTypes.func,
  onMark: PropTypes.func,
  onOpenShared: PropTypes.func,
};

const shortDate = (iso) =>
  iso
    ? new Date(iso).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })
    : null;

/*
  The scenario's edits, newest first. Each says what it did to past results —
  still compare, re-graded or re-run. No version numbers: the environment
  version is the one people pick and compare by.
*/
function ChangeLog({ row }) {
  const [all, setAll] = useState(false);
  const log = [...(row.versionLog || [])].reverse();
  const shown = all ? log : log.slice(0, 3);
  return (
    <Stack spacing={0.5} sx={{ maxWidth: 760 }}>
      {!log.length && (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}
        >
          As written
        </Typography>
      )}
      {shown.length > 0 && (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) max-content max-content",
            columnGap: 1.5,
            rowGap: 0.25,
          }}
        >
          {shown.map((l, i) => (
            <Fragment key={`${l.v}-${i}`}>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.primary",
                  lineHeight: 1.7,
                }}
              >
                {l.why || "Edited"}
              </Typography>
              <Tooltip arrow title={SCENARIO_BUMPS[l.level]?.note || ""}>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    lineHeight: 1.7,
                    cursor: "help",
                  }}
                >
                  {EDIT_EFFECT[l.level] || "Edited"}
                </Typography>
              </Tooltip>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  lineHeight: 1.7,
                }}
              >
                {shortDate(l.at)}
              </Typography>
            </Fragment>
          ))}
        </Box>
      )}
      {log.length > 3 && (
        <Box>
          <InlineLink onClick={() => setAll((o) => !o)}>
            {all ? "Fewer" : `All ${log.length}`}
          </InlineLink>
        </Box>
      )}
    </Stack>
  );
}
ChangeLog.propTypes = { row: PropTypes.object };

/* A proposed scenario — the review step: it joins the environment, and
   starts running, only once someone adds it. */
function ProposedRow({ row, onAction, locked }) {
  const can = onAction && !locked;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      flexWrap="wrap"
      rowGap={0.5}
      sx={{ maxWidth: 760 }}
    >
      <ProposedBadge />
      <Typography
        sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
      >
        {"Joins the environment once you add it — it doesn't run until then"}
      </Typography>
      {can && (
        <TextAction
          onClick={(e) => {
            e.stopPropagation();
            onAction("approve", row);
          }}
        >
          Add
        </TextAction>
      )}
    </Stack>
  );
}
ProposedRow.propTypes = {
  row: PropTypes.object,
  onAction: PropTypes.func,
  locked: PropTypes.bool,
};

/* TASK_COMPLETED → "Task completed" */
const checkName = (label = "") => {
  const words = label.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

/* "Male · 40–50 · Lead Finance Administrator" */
const personaMeta = (p) =>
  [
    p.gender && p.gender.charAt(0).toUpperCase() + p.gender.slice(1),
    p.ageGroup ? String(p.ageGroup).replace("-", "–") : p.age && `Age ${p.age}`,
    p.role,
    !p.gender && p.voice && `${p.voice} voice`,
  ]
    .filter(Boolean)
    .join(" · ");

/*
  The path the conversation should take. In production the category and the
  branch are often the same sentence — it is shown once. A branch that is a
  list of handlers reads as a path of chips.
*/
const conversationPath = (row) => {
  const branch = row.conversationBranch;
  const category = row.branchCategory;
  if (!branch && !category) return null;
  const same =
    typeof branch === "string" && category && branch.trim() === category.trim();
  const text = !branch || same ? category || branch : branch;
  /* Already on the collapsed row, next to the name — don't say it twice. */
  if (
    typeof text === "string" &&
    text.trim() === String(row.summary || row.branchCategory || "").trim()
  )
    return null;
  return { steps: text, category: !branch || same ? null : category || null };
};

/*
  The doc's two questions, answered. `canRun`/`correct` are true, false or
  null (re-checking, or not knowable yet).
*/
const canRunAnswer = (state, env) => {
  if (state.id === "archived")
    return {
      verdict: "Not run",
      text: "archived scenarios stay listed but don't run or count",
    };
  if (state.id === "stale")
    return { verdict: "Yes — but stale", text: state.why };
  if (state.canRun === true)
    return { verdict: "Yes", text: canRunMeaning(env) };
  if (state.canRun === false)
    return {
      verdict: state.id === "later" ? "Not on this version" : "No",
      text: state.why,
      bad: state.id !== "later",
    };
  return { verdict: "Re-checking", text: state.why };
};

const correctAnswer = (state, rule) => {
  const ruleRef = state.ruleChange?.id || rule?.id || null;
  const ruleText = state.ruleChange?.to || rule?.text || null;
  if (state.id === "archived")
    return { verdict: "Not asked", text: "it isn't run", ruleRef, ruleText };
  if (state.id === "stale")
    return {
      verdict: "Not for this agent version",
      text: "it needs a tool the agent no longer has, so it counts toward no decision until it's updated or archived",
      ruleRef,
      ruleText,
    };
  if (state.correct === true) {
    return rule
      ? {
          verdict: "Yes",
          text: "the rule it tests is still the intended behaviour",
          ruleRef,
          ruleText,
        }
      : {
          verdict: "Yes",
          text: "nothing it tests has changed as a spec change",
        };
  }
  if (state.correct === false)
    return { verdict: "No", text: state.why, ruleRef, ruleText, bad: true };
  if (state.ruleChange)
    return { verdict: "Re-checking", text: state.why, ruleRef, ruleText };
  return {
    verdict: "Not known",
    text: "not until it can run again",
    ruleRef,
    ruleText,
  };
};

/* "Yes — …", with the rule id it rests on, quietly, when there is one. */
function Answer({ verdict, text, ruleRef, ruleText, bad = false }) {
  return (
    <Typography
      sx={{
        typography: "s2",
        color: "text.secondary",
        lineHeight: 1.6,
        maxWidth: 760,
      }}
    >
      <Box
        component="span"
        sx={{
          fontWeight: 600,
          color: bad ? "status.danger.fg" : "text.primary",
        }}
      >
        {verdict}
      </Box>
      {text && ` — ${text.replace(/\.$/, "")}`}
      {ruleRef && (
        <>
          {" · "}
          <Tooltip arrow title={ruleText || ""}>
            <Box
              component="span"
              sx={{
                fontFamily: "ui-monospace, Menlo, monospace",
                fontSize: 12,
                color: "text.primary",
                borderBottom: "1px dotted",
                borderColor: "text.disabled",
                cursor: "help",
              }}
            >
              {ruleRef}
            </Box>
          </Tooltip>
        </>
      )}
    </Typography>
  );
}
Answer.propTypes = {
  verdict: PropTypes.string,
  text: PropTypes.string,
  ruleRef: PropTypes.string,
  ruleText: PropTypes.string,
  bad: PropTypes.bool,
};

/* Both questions answered yes, in one line — the why on hover. */
function HealthLine({ canRun, correct }) {
  const item = (label, tip) => (
    <Tooltip
      arrow
      title={
        tip
          ? `${tip.charAt(0).toUpperCase()}${tip.slice(1).replace(/\.$/, "")}.`
          : ""
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ cursor: "help" }}
      >
        <Iconify
          icon="solar:check-circle-linear"
          width={14}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          sx={{
            typography: "s2",
            color: "text.primary",
            fontWeight: 600,
            lineHeight: 1.6,
          }}
        >
          {label}
        </Typography>
      </Stack>
    </Tooltip>
  );
  return (
    <Stack direction="row" alignItems="center" spacing={2}>
      {item("Runs", canRun)}
      {item(
        "Still correct",
        correct?.ruleRef
          ? `${correct.text} · ${correct.ruleRef}`
          : correct?.text,
      )}
    </Stack>
  );
}
HealthLine.propTypes = { canRun: PropTypes.string, correct: PropTypes.object };

/* The state, what it means, and the one thing it asks for — plus Archive. */
function StateActions({ state, row, onAction, locked }) {
  const action = stateActionOf(state);
  const archived = state.id === "archived" ? archivedOf(row) : null;
  const archivedLine = archived
    ? `Archived${archived.at ? ` ${shortDate(archived.at)}` : ""}${archived.note ? ` — ${archived.note}` : ""}`
    : null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      flexWrap="wrap"
      rowGap={0.5}
    >
      <StateChip state={state} />
      {/* The note says what to do; once the button says it, the note is noise. */}
      {(archivedLine || !(onAction && !locked && action)) && (
        <Typography
          sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
        >
          {archivedLine || state.note}
        </Typography>
      )}
      {onAction && !locked && (
        <>
          {action && (
            <TextAction
              onClick={(e) => {
                e.stopPropagation();
                onAction(action, row);
              }}
            >
              {STATE_ACTION_LABEL[action]}
            </TextAction>
          )}
          {state.id !== "archived" && (
            <ArchiveControl
              variant="text"
              onArchive={(note) => onAction("archive", row, note)}
            />
          )}
        </>
      )}
    </Stack>
  );
}
StateActions.propTypes = {
  state: PropTypes.object,
  row: PropTypes.object,
  onAction: PropTypes.func,
  locked: PropTypes.bool,
};

const GATE_ICON = {
  true: "solar:check-circle-linear",
  false: "solar:close-circle-linear",
  null: "solar:question-circle-linear",
};
const NA_ICON = "solar:minus-circle-linear";

/* One gate, as an icon and its name. Red only when it failed. */
function GateMark({ gate, tip }) {
  return (
    <Tooltip arrow title={tip}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.375}
        sx={{ cursor: "help" }}
      >
        <Iconify
          icon={gate.na ? NA_ICON : GATE_ICON[String(gate.ok)]}
          width={14}
          sx={{
            color:
              gate.ok === false
                ? "status.danger.fg"
                : gate.ok
                  ? "text.secondary"
                  : "text.disabled",
            flexShrink: 0,
          }}
        />
        <Typography
          sx={{
            typography: "s2",
            color: gate.ok === null ? "text.subtitle" : "text.secondary",
            lineHeight: 1.6,
            whiteSpace: "nowrap",
          }}
        >
          {gate.label}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
GateMark.propTypes = { gate: PropTypes.object, tip: PropTypes.node };

/* A quiet inline link — "+3 more", "Show details". */
function InlineLink({ onClick, children }) {
  return (
    <Box
      component="button"
      type="button"
      onClick={onClick}
      sx={{
        p: 0,
        border: 0,
        bgcolor: "transparent",
        cursor: "pointer",
        font: "inherit",
        typography: "s2",
        fontWeight: 600,
        color: "text.primary",
        display: "inline-flex",
        alignItems: "center",
        gap: 0.25,
        "&:hover": { textDecoration: "underline" },
      }}
    >
      {children}
    </Box>
  );
}
InlineLink.propTypes = { onClick: PropTypes.func, children: PropTypes.node };

/*
  The gates, then the version they were passed on and the way into the
  machinery. Worded for the world: staged · solvable · not vacuous — or, with
  no world, ready · solvable · not vacuous against a reference transcript.
  Those three stay inline; the admission gates that make the checks
  trustworthy (mutation controls, alternate paths, setup can't earn credit)
  sit behind "+3 more", open on arrival when one of them failed.
*/
function ProofRow({ proof, vl = (l) => l, gates, connect, open, onToggle }) {
  const core = gates.filter((g) => g.core);
  const extra = gates.filter((g) => g.extra);
  const failedExtra = extra.filter((g) => g.ok === false);
  const [all, setAll] = useState(failedExtra.length > 0);
  const tipOf = (g) =>
    g.na
      ? g.detail
      : g.ok === null
        ? `${g.detail} Not yet proved on ${vl(proof.current)}.`
        : g.ok === false && g.reason
          ? g.reason
          : `${g.detail}${g.should ? " (should — not yet required)" : ""}`;
  const sep = (
    <Box component="span" sx={{ color: "text.disabled", typography: "s2" }}>
      ·
    </Box>
  );
  return (
    <Stack spacing={0.25}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        flexWrap="wrap"
        rowGap={0.25}
      >
        {core.map((g, i) => (
          <Fragment key={g.id}>
            {i > 0 && sep}
            <GateMark gate={g} tip={tipOf(g)} />
          </Fragment>
        ))}
        {extra.length > 0 && (
          <>
            {sep}
            <InlineLink onClick={() => setAll((o) => !o)}>
              {all ? "Fewer" : `+${extra.length} more`}
              {!all && failedExtra.length > 0 && (
                <Box
                  component="span"
                  sx={{ color: "status.danger.fg", fontWeight: 600 }}
                >{` · ${failedExtra.length} failed`}</Box>
              )}
            </InlineLink>
          </>
        )}
        {sep}
        <Typography
          sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
        >
          {proof.stale
            ? `not yet proved on ${vl(proof.current)}`
            : `on ${vl(proof.proved)}`}
        </Typography>
        <InlineLink onClick={onToggle}>
          {open ? "Hide details" : "Show details"}
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={12}
          />
        </InlineLink>
      </Stack>
      <Typography
        sx={{ typography: "s3", color: "text.subtitle", maxWidth: 760 }}
      >
        {proof.stale
          ? `${proof.reasons.map((r) => INVALIDATING[r]).join("; ")}.`
          : connect
            ? "Proved against a reference transcript the judge grades — there is no world to stage."
            : "Proved against the world with a reference solution of tool calls."}
      </Typography>
      <Collapse in={all} unmountOnExit>
        <GateList gates={gates} vl={vl} proof={proof} sx={{ mt: 0.75 }} />
      </Collapse>
    </Stack>
  );
}
ProofRow.propTypes = {
  proof: PropTypes.object,
  vl: PropTypes.func,
  gates: PropTypes.array,
  connect: PropTypes.bool,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
};

/* Every gate, one line each: what it proves, or why it failed. */
function GateList({ gates, vl = (l) => l, proof, sx, files = false }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "max-content minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: 0.375,
        maxWidth: 760,
        ...sx,
      }}
    >
      {gates.map((g) => (
        <Fragment key={g.id}>
          <GateMark
            gate={g}
            tip={
              g.na ? g.detail : g.ok === false && g.reason ? g.reason : g.detail
            }
          />
          <Typography
            sx={{
              typography: "s2",
              lineHeight: 1.6,
              color:
                g.ok === false
                  ? "text.primary"
                  : g.na || g.ok === null
                    ? "text.subtitle"
                    : "text.secondary",
              mb: { xs: 0.5, sm: 0 },
            }}
          >
            {g.na
              ? "Doesn't apply — no world to stage or replay against."
              : g.ok === false && g.reason
                ? g.reason
                : g.ok === null
                  ? `Not yet proved on ${vl(proof.current)}.`
                  : g.detail}
            {files && g.file && !g.na && (
              <Box
                component="span"
                sx={{
                  ml: 0.75,
                  color: "text.subtitle",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  fontSize: 12,
                }}
              >
                {g.file}
              </Box>
            )}
          </Typography>
        </Fragment>
      ))}
    </Box>
  );
}
GateList.propTypes = {
  gates: PropTypes.array,
  vl: PropTypes.func,
  proof: PropTypes.object,
  sx: PropTypes.object,
  files: PropTypes.bool,
};

/* The simulated caller's policy, the reference solution and the files. */
function Internals({
  s,
  env,
  policy,
  connect = false,
  brief,
  gates = [],
  proof,
  vl,
}) {
  /* With no world, the proof is a transcript the judge grades, and the
     folder holds the fixture's caller account rather than a setup script.
     The admission gates each leave a file behind — the ones that apply. */
  const gateFiles = gates
    .filter((g) => g.extra && !g.na && g.file)
    .map((g) => g.file);
  const files = connect
    ? [
        "scenario.json",
        "fixture_account.json",
        "reference_transcript.json",
        ...gateFiles,
        ...s.checks.map((c) => `judge/${c.id}.yaml`),
      ]
    : [
        ...(s.files || []),
        ...gateFiles.filter((f) => !(s.files || []).includes(f)),
      ];
  return (
    <Stack
      spacing={2}
      divider={
        <Box sx={{ borderBottom: "1px dashed", borderColor: "divider" }} />
      }
      sx={{
        mt: 2,
        pt: 2,
        borderTop: "1px dashed",
        borderColor: "divider",
        "& p, & li": { maxWidth: 780 },
      }}
    >
      {policy && (
        <Section title="The simulated caller — policy, not a prompt">
          <Stack spacing={1.25}>
            {/* Usually the brief, word for word — shown above already. */}
            {policy.goal && policy.goal !== s.task && (
              <PolicyRow label="Goal">{policy.goal}</PolicyRow>
            )}
            <PolicyRow label="States plainly">
              <Stack component="ul" sx={{ m: 0, pl: 2 }}>
                {policy.facts.map((f) => (
                  <Typography
                    key={f}
                    component="li"
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {f}
                  </Typography>
                ))}
              </Stack>
            </PolicyRow>
            <PolicyRow label="Held back">
              <Stack spacing={0.375}>
                {policy.private.map((f) => (
                  <Stack
                    key={f.fact}
                    direction="row"
                    spacing={0.75}
                    alignItems="flex-start"
                  >
                    <Iconify
                      icon="solar:lock-keyhole-minimalistic-linear"
                      width={14}
                      sx={{
                        color: "status.warning.fg",
                        flexShrink: 0,
                        mt: "2px",
                      }}
                    />
                    <Typography
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      <Box
                        component="span"
                        sx={{ color: "text.primary", fontWeight: 600 }}
                      >
                        {f.fact}
                      </Box>{" "}
                      — {f.trigger}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </PolicyRow>
            <PolicyRow label="Manner">
              {policy.style.verbosity}, {policy.style.patience} patience,{" "}
              {policy.style.precision} precision
              {" · "}
              {policy.objections.style.toLowerCase()} (max{" "}
              {policy.objections.max}){" · "}
              {policy.interruption.allowed
                ? policy.interruption.when
                : "does not interrupt"}
            </PolicyRow>
            <PolicyRow label="Hangs up when">
              <Stack component="ul" sx={{ m: 0, pl: 2 }}>
                {policy.termination.map((t) => (
                  <Typography
                    key={t}
                    component="li"
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {t}
                  </Typography>
                ))}
              </Stack>
            </PolicyRow>
            <PolicyRow label="Never">
              <Stack spacing={0.25}>
                {policy.prohibited.map((t) => (
                  <Typography
                    key={t}
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    · {t}
                  </Typography>
                ))}
              </Stack>
            </PolicyRow>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              The caller is never tuned to make the agent pass. It follows this
              policy, and if that exposes the agent, that is the point.
            </Typography>
          </Stack>
        </Section>
      )}

      {connect ? (
        <Section title="The reference transcript — the judge passes it, so it can be passed; never played to the agent">
          <Stack spacing={0.375}>
            {[
              { who: "Caller", text: brief?.intro || s.task },
              ...s.reference
                .filter((r) => r.tool !== "respond")
                .map((r) => ({
                  who: "Agent",
                  text: `uses ${r.tool}${Object.keys(r.args).length ? ` with ${Object.keys(r.args).join(", ")}` : ""}`,
                })),
              ...(s.expected
                ? [{ who: "Judge", text: `passes — ${asSentence(s.expected)}` }]
                : []),
            ]
              .filter((l) => l.text)
              .map((l, i) => (
                <Stack
                  key={i}
                  direction="row"
                  spacing={1}
                  alignItems="flex-start"
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      color: "text.subtitle",
                      width: 52,
                      flexShrink: 0,
                    }}
                  >
                    {l.who}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      color: "text.secondary",
                      minWidth: 0,
                    }}
                  >
                    {l.text}
                  </Typography>
                </Stack>
              ))}
          </Stack>
        </Section>
      ) : (
        <Section title="The reference solution — proves it can be passed, never run against the agent">
          <Stack spacing={0.375}>
            {s.reference.map((r, i) => (
              <Typography
                key={i}
                sx={{
                  typography: "s2",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  color: "text.secondary",
                }}
              >
                {i + 1}. {r.tool}
                {Object.keys(r.args).length > 0 && (
                  <Box component="span" sx={{ color: "text.subtitle" }}>
                    {" "}
                    {Object.keys(r.args).join(", ")}
                  </Box>
                )}
              </Typography>
            ))}
          </Stack>
        </Section>
      )}

      {gates.length > 0 && proof && (
        <Section title="The quality gate — every scenario passes all of these before it joins the set; a failure quarantines it">
          <GateList gates={gates} vl={vl} proof={proof} files />
        </Section>
      )}

      <Section title="Its folder">
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontFamily: "ui-monospace, Menlo, monospace",
            mb: 0.75,
          }}
        >
          {scenarioFolder(env, s)}
        </Typography>
        <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
          {files.map((f) => (
            <Typography
              key={f}
              sx={{
                px: 0.875,
                py: 0.375,
                borderRadius: "4px",
                typography: "s3",
                fontFamily: "ui-monospace, Menlo, monospace",
                color: "text.secondary",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              {f}
            </Typography>
          ))}
        </Stack>
      </Section>
    </Stack>
  );
}
Internals.propTypes = {
  s: PropTypes.object,
  env: PropTypes.object,
  policy: PropTypes.object,
  connect: PropTypes.bool,
  brief: PropTypes.object,
  gates: PropTypes.array,
  proof: PropTypes.object,
  vl: PropTypes.func,
};

/* ── where it came from ──────────────────────────────────────────────────── */

const hasOrigin = (row) =>
  !!(
    row?.personalData ||
    row?.productionReplay ||
    row?.reproduce ||
    row?.variation ||
    row?.groupId ||
    row?.fromImprovement
  );

function OriginRow({ row }) {
  const replay = row.productionReplay;
  const v = row.variation;
  return (
    <Stack spacing={0.75} sx={{ maxWidth: 760 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        flexWrap="wrap"
        rowGap={0.5}
      >
        {row.reproduce && (
          <ReproTag repro={row.reproduce} target={!!row.target} />
        )}
        <PersonalDataTag row={row} />
        {row.groupId && (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", lineHeight: 1.6 }}
          >
            {row.groupId === row.id
              ? "Source of its scenario group"
              : `Scenario group · ${v?.ofName || row.groupId}`}
          </Typography>
        )}
      </Stack>
      {v && (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}
        >
          {`Variation ${v.n} of ${v.ofName}: ${[v.persona && `caller ${v.persona}`, v.wording && v.wording.toLowerCase(), v.world?.record && `record ${v.world.record}`].filter(Boolean).join(", ")} — the same goal and sub-goals.`}
          {row.variationCheck?.agentVersion && (
            <Box component="span" sx={{ color: "text.subtitle" }}>
              {` Failed ${row.variationCheck.fails} of ${row.variationCheck.of} on agent ${row.variationCheck.agentVersion} before it was added.`}
            </Box>
          )}
        </Typography>
      )}
      {row.fromImprovement && (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}
        >
          {`Written by ${row.fromImprovement.name || row.fromImprovement.id} and added to the environment by a person.`}
        </Typography>
      )}
      {row.personalData && (
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", lineHeight: 1.6 }}
        >
          {personalDataDetail(row)}
        </Typography>
      )}
      {replay?.turns?.length > 0 && (
        <Box>
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              lineHeight: 1.6,
              mb: 0.25,
            }}
          >
            {REPLAY_NOTE}
          </Typography>
          {replay.turns.map((t) => (
            <Stack
              key={t.n}
              direction="row"
              spacing={1.25}
              alignItems="baseline"
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.disabled",
                  width: 52,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`Caller ${t.n}`}
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  lineHeight: 1.6,
                }}
              >
                {t.text}
              </Typography>
            </Stack>
          ))}
        </Box>
      )}
    </Stack>
  );
}
OriginRow.propTypes = { row: PropTypes.object };
