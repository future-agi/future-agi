import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tooltip,
  TextField,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import { SectionCard, EmptyState } from "../components/primitives";
import {
  rulesOf,
  subGoalCatalogOf,
  catalogVersionOf,
  subGoalsOfScenario,
} from "../_mock/envModel";
import {
  KIND_META,
  TESTS_LABEL,
  readsNote,
  defaultCriterionOf,
  quotesPromptVerbatim,
  idForUseCase,
  caseRefsOf,
  markSplitOf,
  SUBGOAL_EDIT_KINDS,
  subGoalHistoryOf,
  subGoalVersionOf,
  subGoalVersionLabel,
  quotedPromptTextOf,
  publishSubGoalPatch,
} from "../_mock/subGoalCatalog";
import { currentUser } from "../_mock/scenarioProvenance";
import { MARK_NOTE } from "../_mock/scenarioSubGoals";
import { clickable, focusRing } from "../components/clickable";
import { goalLabel, scenarioTitle } from "../_mock/scenarios";

const MONO = "ui-monospace, Menlo, monospace";
const VERBATIM_NOTE =
  "Quotes the prompt word for word — a wording change will fail it.";

/**
 * Sub-goals — the shared, named checks the suite grades with.
 *
 * One entry per meaning, however many scenarios name it, so "Calls
 * lookup_order with the right arguments" is defined once and graded the same
 * way everywhere. Each says what it tests (a rule, a tool, a use case), which
 * is what lets a rule change point at the checks — and through them the
 * scenarios — it affects.
 *
 * Each is versioned on its own (lookup_order@1 → @2) and never edited in
 * place (PRD v7 AC-18.10–18.12). An edit asks first whether it is a FIX (a
 * bug in the check, same intent — scenarios using it take a minor bump and
 * past runs are re-graded) or a POLICY change (what's right changed —
 * published marked as policy; compare reports it as criteria changed). The
 * drawer shows how many scenarios that touches before saving.
 *
 * Sub-goals live on the scenario (PRD v6) — each scenario says which it has
 * and whether each is critical or supporting — so this is a view of the
 * suite by sub-goal, rendered inside the Scenarios tab with `embedded`.
 */
export default function SubGoalsPanel({
  env,
  envState,
  patch,
  onGo,
  locked = false,
  embedded = false,
  openGoalId = null,
  onGoalClosed,
}) {
  const [openId, setOpenId] = useState(openGoalId);
  /* A scenario's "shared · used by N" link opens straight onto its definition. */
  useEffect(() => {
    if (openGoalId) setOpenId(openGoalId);
  }, [openGoalId]);
  const rules = useMemo(() => rulesOf(env, envState), [env, envState]);
  const catalog = useMemo(
    () => subGoalCatalogOf(env, envState),
    [env, envState],
  );
  const marks = useMemo(() => markSplitOf(env, envState), [env, envState]);
  const open = catalog.find((g) => g.id === openId) || null;

  const save = (goal, { name, criterion, kind, note }) => {
    patch(
      publishSubGoalPatch(envState, goal, {
        name,
        criterion,
        kind,
        note,
        by: currentUser().name,
      }),
    );
  };

  return (
    <Box sx={embedded ? undefined : { p: 2 }}>
      {/* No heading — the tab above already names the page. Embedded, the
          host owns the header. */}
      {!embedded && (
        <Typography sx={{ typography: "s2", color: "text.secondary", mb: 2 }}>
          Shared, named checks every scenario grades with
        </Typography>
      )}

      <SectionCard bare>
        {catalog.length === 0 ? (
          <EmptyState
            icon="solar:checklist-minimalistic-linear"
            title="No sub-goals yet"
            body="Sub-goals come from the scenarios — each check a scenario grades with is collected here once, however many scenarios use it."
            action={
              onGo && (
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={() => onGo("scenarios")}
                  endIcon={
                    <Iconify icon="solar:arrow-right-linear" width={16} />
                  }
                >
                  Add scenarios
                </Button>
              )
            }
          />
        ) : (
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small" sx={{ minWidth: 880 }}>
              <TableHead>
                <TableRow
                  sx={{
                    "&:last-of-type .MuiTableCell-root": {
                      borderColor: "divider",
                    },
                  }}
                >
                  {["Name", "Kind", "Tests", "Used by", "Last change"].map(
                    (h, i) => (
                      <TableCell
                        key={h}
                        align={i >= 3 ? "right" : "left"}
                        sx={{
                          fontSize: 13,
                          fontWeight: 500,
                          color: "text.secondary",
                          bgcolor: "background.paper",
                          backgroundImage: "none",
                          borderBottom: "1px solid",
                          borderColor: "divider",
                          whiteSpace: "nowrap",
                          ...(i === 0 && { pl: 2.5 }),
                          ...(i === 4 && { pr: 2.5 }),
                        }}
                      >
                        {h}
                      </TableCell>
                    ),
                  )}
                </TableRow>
              </TableHead>
              <TableBody>
                {catalog.map((g) => (
                  <TableRow
                    key={g.id}
                    hover
                    {...clickable(() => setOpenId(g.id), { role: "row" })}
                    sx={{
                      ...focusRing,
                      cursor: "pointer",
                      "& .MuiTableCell-body": {
                        borderColor: "divider",
                        verticalAlign: "middle",
                      },
                      "&:last-of-type .MuiTableCell-body": {
                        borderBottom: "none",
                      },
                    }}
                  >
                    <TableCell sx={{ pl: 2.5, maxWidth: 420 }}>
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 600 }}
                        title={g.name}
                      >
                        {g.name}
                      </Typography>
                      {quotesPromptVerbatim(g) ? (
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={0.5}
                        >
                          <Iconify
                            icon="solar:danger-triangle-linear"
                            width={12}
                            sx={{ color: "text.tertiary", flexShrink: 0 }}
                          />
                          <Typography
                            noWrap
                            sx={{ typography: "s3", color: "text.secondary" }}
                          >
                            {VERBATIM_NOTE}
                          </Typography>
                        </Stack>
                      ) : (
                        <Typography
                          noWrap
                          sx={{
                            typography: "s3",
                            color: "text.disabled",
                            fontFamily: MONO,
                            fontSize: 12,
                          }}
                        >
                          {g.id}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <KindCell goal={g} />
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <TestsCell tests={g.tests} />
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                      <Typography
                        sx={{
                          typography: "s2",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {g.usedBy.length}
                        <Box component="span" sx={{ color: "text.tertiary" }}>
                          {" "}
                          scenario{g.usedBy.length === 1 ? "" : "s"}
                        </Box>
                      </Typography>
                      <MarkSplit split={marks.get(g.id)} />
                    </TableCell>
                    <TableCell
                      align="right"
                      sx={{ pr: 2.5, whiteSpace: "nowrap" }}
                    >
                      <VersionCell envState={envState} id={g.id} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </SectionCard>

      <SubGoalDrawer
        goal={open}
        split={open ? marks.get(open.id) : null}
        env={env}
        envState={envState}
        rules={rules}
        locked={locked}
        onClose={() => {
          setOpenId(null);
          onGoalClosed?.();
        }}
        onSave={save}
        onGo={onGo}
      />
    </Box>
  );
}

SubGoalsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
  embedded: PropTypes.bool,
  openGoalId: PropTypes.string,
  onGoalClosed: PropTypes.func,
};

/* ── cells ───────────────────────────────────────────────────────────────── */

function KindCell({ goal }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.75}>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {KIND_META[goal.kind]?.label}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {goal.kind === "code" ? readsNote(goal) : KIND_META.judged.note}
      </Typography>
    </Stack>
  );
}
KindCell.propTypes = { goal: PropTypes.object };

/* "Rule R3", "Tool lookup_order", "Use case UC-1slg" — the kind in words, the
   thing it points at in mono, because that's the id the rest of the product
   uses for it. */
function TestsCell({ tests }) {
  const cases = tests?.kind === "use case" ? caseRefsOf(tests) : [];
  /* A check shared across use cases names how many; one use case names it. */
  const ref =
    tests?.kind === "use case"
      ? cases.length === 1
        ? idForUseCase(cases[0])
        : cases.length > 1
          ? `${cases.length} use cases`
          : null
      : tests?.ref;
  const body = (
    <Stack direction="row" alignItems="baseline" spacing={0.75}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {cases.length > 1 ? "Use cases" : TESTS_LABEL[tests?.kind] || "—"}
      </Typography>
      {ref && (
        <Typography
          sx={{
            typography: "s3",
            fontFamily: cases.length > 1 ? undefined : MONO,
            color: "text.primary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {cases.length > 1 ? cases.length : ref}
        </Typography>
      )}
    </Stack>
  );
  return cases.length ? (
    <Tooltip
      arrow
      title={
        <Box>
          {cases.map((c) => (
            <Typography key={c} sx={{ typography: "s3" }}>
              {idForUseCase(c)} · {goalLabel(c)}
            </Typography>
          ))}
        </Box>
      }
    >
      <Box component="span" sx={{ display: "inline-flex" }}>
        {body}
      </Box>
    </Tooltip>
  ) : (
    body
  );
}
TestsCell.propTypes = { tests: PropTypes.object };

/* Whether the sub-goal was ever changed — a fix or a policy change, and when.
   Its own version (@2) is shown when the sub-goal is opened, not in the list. */
function VersionCell({ envState, id }) {
  const last = subGoalHistoryOf(envState, id).slice(-1)[0];
  if (!last)
    return (
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        As written
      </Typography>
    );
  return (
    <Box>
      <Typography sx={{ typography: "s3", color: "text.primary" }}>
        {last.kind === "policy" ? "Policy change" : "Grader fix"}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {new Date(last.at).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        })}
      </Typography>
    </Box>
  );
}
VersionCell.propTypes = { envState: PropTypes.object, id: PropTypes.string };

function MarkSplit({ split }) {
  if (!split) return null;
  return (
    <Typography
      sx={{
        typography: "s3",
        color: "text.tertiary",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      critical in {split.critical} · supporting in {split.supporting}
    </Typography>
  );
}
MarkSplit.propTypes = { split: PropTypes.object };

/* ── drawer — what it checks, what it tests, who uses it, and editing ───── */

function SubGoalDrawer({
  goal,
  split,
  env,
  envState,
  rules,
  locked,
  onClose,
  onSave,
  onGo,
}) {
  return (
    <SideDrawer open={!!goal} onClose={onClose} width={480}>
      {/* Keyed so opening another entry, or a save, starts the form fresh. */}
      {goal && (
        <SubGoalDetail
          key={`${goal.id}:${subGoalVersionOf(envState, goal.id)}`}
          goal={goal}
          split={split}
          env={env}
          envState={envState}
          rules={rules}
          locked={locked}
          onClose={onClose}
          onSave={onSave}
          onGo={onGo}
        />
      )}
    </SideDrawer>
  );
}
SubGoalDrawer.propTypes = {
  goal: PropTypes.object,
  split: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  rules: PropTypes.array,
  locked: PropTypes.bool,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
  onGo: PropTypes.func,
};

function SubGoalDetail({
  goal,
  split,
  env,
  envState,
  rules,
  locked,
  onClose,
  onSave,
  onGo,
}) {
  const current = {
    name: goal.name,
    criterion: goal.criterion || defaultCriterionOf(goal, rules),
  };
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(current);
  /* Fix or policy change — asked before anything is published. */
  const [kind, setKind] = useState(null);
  const dirty =
    draft.name.trim() !== current.name ||
    draft.criterion.trim() !== current.criterion;
  const valid = !!draft.name.trim() && !!draft.criterion.trim() && !!kind;

  const vNow = subGoalVersionOf(envState, goal.id);
  const label = subGoalVersionLabel(envState, goal.id);
  const next = subGoalVersionLabel(envState, goal.id, vNow + 1);
  const history = subGoalHistoryOf(envState, goal.id);
  const quoted = quotedPromptTextOf(
    { ...goal, criterion: current.criterion },
    rules,
  );
  const n = goal.usedBy.length;
  const scenarios = useMemo(() => {
    const byId = new Map((envState.scenarios || []).map((r) => [r.id, r]));
    return goal.usedBy
      .map((id) => byId.get(id))
      .filter(Boolean)
      .map((row) => ({
        row,
        mark:
          subGoalsOfScenario(row, env, envState).find((x) => x.id === goal.id)
            ?.mark || null,
      }));
  }, [goal.id, goal.usedBy, env, envState]);
  const verbatim = quotesPromptVerbatim(
    { ...goal, name: editing ? draft.name : goal.name },
    editing ? draft.criterion : current.criterion,
  );

  const save = () => {
    onSave(goal, {
      name: draft.name.trim(),
      criterion: draft.criterion.trim(),
      kind,
    });
    setEditing(false);
    setKind(null);
  };
  const cancel = () => {
    setDraft(current);
    setEditing(false);
    setKind(null);
  };

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            {goal.name}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            <Box component="span" sx={{ fontFamily: MONO }}>
              {label}
            </Box>
            {" · "}
            {KIND_META[goal.kind]?.label}
            {" · "}catalogue{" "}
            <Box component="span" sx={{ fontFamily: MONO }}>
              {catalogVersionOf(envState)}
            </Box>
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify
            icon="solar:close-circle-linear"
            width={20}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      </Stack>

      <Stack spacing={2.5} sx={{ p: 2.5, flex: 1, overflowY: "auto" }}>
        <Box>
          <Label>What it checks</Label>
          {editing ? (
            <Stack spacing={1.75} sx={{ mt: 0.5 }}>
              <TextField
                size="small"
                label="Name"
                fullWidth
                value={draft.name}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, name: e.target.value }))
                }
              />
              <TextField
                size="small"
                label={
                  goal.kind === "judged"
                    ? "Criterion the judge reads"
                    : "What passes"
                }
                fullWidth
                multiline
                minRows={4}
                value={draft.criterion}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, criterion: e.target.value }))
                }
                helperText={
                  goal.kind === "judged"
                    ? "Describe the behaviour, not the words — the prompt is reworded between agent versions."
                    : undefined
                }
              />
            </Stack>
          ) : (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {current.criterion}
            </Typography>
          )}
          {verbatim && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 1 }}
            >
              <Iconify
                icon="solar:danger-triangle-linear"
                width={14}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {VERBATIM_NOTE}
              </Typography>
            </Stack>
          )}
        </Box>

        <Box>
          <Label>How it grades</Label>
          <Typography sx={{ typography: "s2" }}>
            <Box component="span" sx={{ fontWeight: 600 }}>
              {KIND_META[goal.kind]?.label}
            </Box>
            <Box component="span" sx={{ color: "text.secondary" }}>
              {" — "}
              {goal.kind === "code"
                ? readsNote(goal)
                : "an LLM reads the transcript against the criterion above"}
            </Box>
          </Typography>
        </Box>

        <Box>
          <Label>What it tests</Label>
          <TestsDetail tests={goal.tests} env={env} rules={rules} />
        </Box>

        {/* The prompt text it quotes — what a reworded rule would touch. */}
        <Box>
          <Label>Prompt text it quotes</Label>
          {quoted ? (
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                pl: 1.25,
                borderLeft: "2px solid",
                borderColor: "divider",
              }}
            >
              {`“${quoted.replace(/\.$/, "")}”`}
            </Typography>
          ) : (
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              None — it checks an outcome, not wording.
            </Typography>
          )}
        </Box>

        <Box>
          <Label>{`Versions · ${label}`}</Label>
          <Stack spacing={0.375}>
            {[...history].reverse().map((h) => (
              <Stack
                key={`${h.n}-${h.at}`}
                direction="row"
                spacing={1.25}
                alignItems="baseline"
              >
                <Typography
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    color: "text.primary",
                    width: 28,
                    flexShrink: 0,
                  }}
                >{`@${h.n}`}</Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {`${SUBGOAL_EDIT_KINDS[h.kind].label}${h.kind === "policy" ? " — reported as criteria changed" : " — scenarios re-graded"}`}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  {new Date(h.at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}
                </Typography>
              </Stack>
            ))}
            <Stack direction="row" spacing={1.25} alignItems="baseline">
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  color: history.length ? "text.tertiary" : "text.primary",
                  width: 28,
                  flexShrink: 0,
                }}
              >
                @1
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                As written when the scenarios were built
              </Typography>
            </Stack>
          </Stack>
        </Box>

        <Box>
          <Stack
            direction="row"
            alignItems="baseline"
            justifyContent="space-between"
          >
            <Label>
              Used by {n} scenario{n === 1 ? "" : "s"}
            </Label>
            {onGo && (
              <Button
                size="small"
                onClick={() => {
                  onClose();
                  onGo("scenarios");
                }}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
                sx={{ color: "text.secondary", minWidth: 0, mt: -0.5 }}
              >
                Scenarios
              </Button>
            )}
          </Stack>
          {/* Each scenario says how much this check matters to it. */}
          {split && (
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                mt: -0.25,
                mb: 0.75,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              Critical in {split.critical} · supporting in {split.supporting}
              {
                " · critical fails the conversation if it doesn't hold; supporting is reported, doesn't fail it"
              }
            </Typography>
          )}
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              maxHeight: 280,
              overflowY: "auto",
            }}
          >
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
            >
              {scenarios.map(({ row, mark }) => (
                <Stack
                  key={row.id}
                  direction="row"
                  alignItems="center"
                  spacing={1.25}
                  sx={{ px: 1.5, py: 0.875 }}
                >
                  <Typography
                    noWrap
                    sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                  >
                    {scenarioTitle(row.title) || row.name}
                  </Typography>
                  {row.persona?.name && (
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        flexShrink: 0,
                        maxWidth: 140,
                      }}
                    >
                      {row.persona.name}
                    </Typography>
                  )}
                  {mark && (
                    <Tooltip
                      arrow
                      title={
                        MARK_NOTE[
                          mark === "critical" ? "critical" : "supporting"
                        ]
                      }
                    >
                      <Typography
                        sx={{
                          typography: "s3",
                          color:
                            mark === "critical"
                              ? "text.primary"
                              : "text.tertiary",
                          fontWeight: mark === "critical" ? 600 : 400,
                          flexShrink: 0,
                          width: 64,
                          textAlign: "right",
                          cursor: "default",
                        }}
                      >
                        {mark === "critical" ? "Critical" : "Supporting"}
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
              ))}
            </Stack>
          </Box>
        </Box>
      </Stack>

      {/* Fix or policy — asked, then the impact, right above the save, so it
          is read before the click that makes it true. Never in place: it
          publishes the next version. */}
      {editing && dirty && (
        <Box
          sx={{
            mx: 2.5,
            mb: 1.5,
            p: 1.5,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "action.hover",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Is this a fix or a policy change?
          </Typography>
          <Stack direction="row" spacing={0.75} sx={{ mt: 0.75 }}>
            {Object.entries(SUBGOAL_EDIT_KINDS).map(([id, k]) => {
              const on = kind === id;
              return (
                <ButtonBase
                  key={id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setKind(id)}
                  sx={{
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "flex-start",
                    justifyContent: "flex-start",
                    textAlign: "left",
                    p: 1,
                    borderRadius: 1,
                    font: "inherit",
                    cursor: "pointer",
                    border: "1px solid",
                    borderColor: on ? "text.primary" : "divider",
                    bgcolor: on ? "background.paper" : "transparent",
                  }}
                >
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {k.label}
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {k.ask}
                  </Typography>
                </ButtonBase>
              );
            })}
          </Stack>
          {kind && (
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 1 }}
            >
              {`Publishes ${next}${kind === "policy" ? " marked as a policy change" : " as a grader fix"}, used by ${n} scenario${n === 1 ? "" : "s"}. `}
              {SUBGOAL_EDIT_KINDS[kind].note}
              {` ${label} stays on every past run.`}
            </Typography>
          )}
        </Box>
      )}

      <Stack
        direction="row"
        spacing={1.5}
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Box flex={1} />
        {editing ? (
          <>
            <Button
              size="small"
              onClick={cancel}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!dirty || !valid}
              onClick={save}
            >
              {kind ? `Publish ${next}` : "Publish"}
            </Button>
          </>
        ) : (
          <Tooltip arrow title={locked ? "Fork this environment to edit." : ""}>
            <span>
              <Button
                size="small"
                variant="outlined"
                disabled={locked}
                onClick={() => setEditing(true)}
                startIcon={<Iconify icon="solar:pen-linear" width={14} />}
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  "&:hover": { borderColor: "text.disabled" },
                }}
              >
                Edit
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>
    </Stack>
  );
}
SubGoalDetail.propTypes = SubGoalDrawer.propTypes;

function TestsDetail({ tests, env, rules }) {
  const kind = tests?.kind;
  const cases = kind === "use case" ? caseRefsOf(tests) : [];
  if (cases.length > 1) {
    return (
      <Box>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 0.75 }}>
          {cases.length} use cases — a shared check, graded the same way in each
        </Typography>
        <Stack spacing={0.5}>
          {cases.map((c) => (
            <Box
              key={c}
              sx={{
                display: "grid",
                gridTemplateColumns: "72px 1fr",
                columnGap: 1.25,
                alignItems: "baseline",
              }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  color: "text.tertiary",
                }}
              >
                {idForUseCase(c)}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {goalLabel(c)}
              </Typography>
            </Box>
          ))}
        </Stack>
      </Box>
    );
  }
  const rule = kind === "rule" ? rules.find((r) => r.id === tests.ref) : null;
  const tool =
    kind === "tool"
      ? (env?.tools || []).find((t) => t.name === tests.ref)
      : null;
  const id =
    kind === "use case"
      ? tests.ref
        ? idForUseCase(tests.ref)
        : null
      : tests?.ref;
  /* Ids stay keyed on the stored text; only the words shown are reworded. */
  const text =
    rule?.text ||
    tool?.desc ||
    (kind === "use case" ? goalLabel(tests.ref) : null);
  return (
    <Stack direction="row" spacing={1.25} alignItems="baseline">
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          flexShrink: 0,
          minWidth: 56,
        }}
      >
        {TESTS_LABEL[kind] || "Nothing recorded"}
      </Typography>
      <Box minWidth={0}>
        {id && (
          <Typography sx={{ typography: "s3", fontFamily: MONO }}>
            {id}
          </Typography>
        )}
        {text && (
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {text}
          </Typography>
        )}
        {kind === "rule" && (
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            From the agent&apos;s prompt. If a new agent version changes this
            rule as a spec change, this check changes with it.
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
TestsDetail.propTypes = {
  tests: PropTypes.object,
  env: PropTypes.object,
  rules: PropTypes.array,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        textTransform: "none",
        letterSpacing: 0.4,
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
