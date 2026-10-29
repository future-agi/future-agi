import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  ButtonBase,
  Button,
  TextField,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import {
  episodeContract,
  contractToolsOf,
  isArgLimit,
  contractUseCasesOf,
  contractEditPlan,
} from "../_mock/rlContract";
import { rulesOf, subGoalCatalogOf, displayEnvLabel } from "../_mock/envModel";
import { environmentVersions } from "../_mock/versions";
import { ruleCoverageOf } from "../_mock/subGoalCatalog";
import { firstSnapshotOfWorld, nextSnapshotLabel } from "../_mock/envSnapshot";
import BuildStreamNote from "../environments/BuildStreamNote";
import ToolArgsGrid from "./ToolArgsGrid";
import { goalLabel } from "../_mock/scenarios";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * The environment contract — what the agent must hold to, and how an episode
 * runs.
 *
 * It leads with the rules, because they are what the suite is graded
 * against: each has an id that survives the contract being re-written, and
 * says which sub-goals test it. Use cases carry ids for the same reason —
 * results group by the id. Then, concrete over formal: when a run ends, who
 * else acts in it, and what scores it (the evaluations, which live on their
 * own tab). The world it runs in —
 * data and tools, with every tool's arguments — is the World tab; only the
 * argument limits, which decide what counts as the agent's mistake, are here. The five-part RL-textbook
 * framing that used to live here — modality adapter, obs/action spaces,
 * transition dynamics, reward spec — mostly duplicated other tabs in
 * RL notation. Removed so the tab reads as "what this env is made of"
 * rather than "our RL spec, restated". The RL vocabulary lives in
 * `_mock/rlContract.js` if it needs to come back behind a toggle.
 */
export default function RlContractPanel({
  env,
  envState,
  patch,
  onGo,
  locked = false,
  buildProgress = null,
  buildDone = null,
}) {
  const episode = episodeContract(env, envState);
  const rules = useMemo(() => rulesOf(env, envState), [env, envState]);
  /* Opened from a failed check (?rule=R2): that rule, scrolled to and lit. */
  const [params] = useSearchParams();
  const litRule = params.get("rule");
  useEffect(() => {
    if (!litRule) return;
    document
      .getElementById(`rule-${litRule}`)
      ?.scrollIntoView({ block: "center" });
  }, [litRule]);
  const coverage = useMemo(
    () => ruleCoverageOf(rules, subGoalCatalogOf(env, envState)),
    [rules, env, envState],
  );
  /* Stored labels read as major.minor ("v3" → "v2.1"). */
  const envVersions = useMemo(
    () => environmentVersions(env, envState),
    [env, envState],
  );
  /* A world version as the environment version that introduced it ("E v3" → "v6"). */
  const worldShown = (id) =>
    firstSnapshotOfWorld(env, envState, id)?.label ||
    displayEnvLabel(id, envVersions);
  /* Use cases and tool arguments, with the corrections made since the build. */
  const useCases = useMemo(
    () => contractUseCasesOf(env, envState),
    [env, envState],
  );
  const tools = useMemo(() => contractToolsOf(env, envState), [env, envState]);
  /* Edits go through the same path as the chat's: cost line first, then a
     new environment version, and the scenarios that depend on it Re-check. */
  const [editing, setEditing] = useState(null);
  /* On the build screen the contract is written as the agent is read: tools
     as their signatures land, then rules; the rest once reading is done. */
  const inBuild = Array.isArray(buildDone) && !buildDone.includes("understand");
  const canEdit = !!patch && !locked && !inBuild;
  const shownTools = inBuild
    ? tools.slice(0, buildProgress?.tools || 0)
    : tools;
  const shownRules = inBuild
    ? rules.slice(0, buildProgress?.rules || 0)
    : rules;
  /* The limits only — each tool's full arguments are on World → Tools. */
  const limits = shownTools.flatMap((t) =>
    t.args.filter(isArgLimit).map((a) => ({ ...a, tool: t.name })),
  );

  return (
    <Box sx={{ p: 2 }}>
      {/* No heading — the tab names the page; its description (and how an
          edit makes a new environment version) is the tab's hover hint. */}
      {inBuild &&
        ((buildProgress?.tools || 0) < tools.length ? (
          <BuildStreamNote
            label="Writing the contract — reading tool signatures"
            count={buildProgress?.tools || 0}
            total={tools.length}
          />
        ) : (
          <BuildStreamNote
            label="Writing the contract — separating the rules"
            count={buildProgress?.rules || 0}
            total={rules.length}
          />
        ))}

      {/* ── rules — stable ids, and what tests each ───────────────────── */}
      {(!inBuild || shownRules.length > 0) && (
        <SectionCard
          title={`Rules (${shownRules.length})`}
          subtitle={
            "From the agent's prompt. When a new agent version changes one, it's either a fix — the suite stays — " +
            "or a spec change — the rule, its sub-goals and their scenarios change. Someone confirms which."
          }
          action={
            canEdit && (
              <AddButton
                label="Add rule"
                onClick={() =>
                  setEditing({ target: "rule", op: "add", text: "" })
                }
              />
            )
          }
          sx={{ mb: 2 }}
        >
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {shownRules.map((r) => (
              <Box
                key={r.id}
                id={`rule-${r.id}`}
                sx={{ bgcolor: r.id === litRule ? "action.selected" : null }}
              >
                <RuleRow
                  rule={r}
                  changedIn={worldShown(r.changedIn)}
                  coverage={coverage.get(r.id)}
                  onGo={onGo}
                  onEdit={
                    canEdit
                      ? () =>
                          setEditing({
                            target: "rule",
                            op: "edit",
                            id: r.id,
                            text: r.text,
                          })
                      : null
                  }
                  onRemove={
                    canEdit
                      ? () =>
                          setEditing({
                            target: "rule",
                            op: "remove",
                            id: r.id,
                            text: r.text,
                          })
                      : null
                  }
                />
              </Box>
            ))}
          </Stack>
        </SectionCard>
      )}

      {/* ── argument limits — the values a call has to stay inside ─────── */}
      {limits.length > 0 && (
        <SectionCard
          title={`Argument limits (${limits.length})`}
          subtitle="Values a tool call has to stay inside — a call outside them is the agent's mistake, not the world's. Every tool's arguments are on World → Tools."
          action={
            onGo &&
            !inBuild && (
              <Button
                size="small"
                onClick={() => onGo("world")}
                sx={{
                  color: "text.secondary",
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                }}
              >
                View tools
              </Button>
            )
          }
          sx={{ mb: 2 }}
        >
          <Box sx={{ px: 2.5, py: 1.5 }}>
            <ToolArgsGrid
              args={limits}
              rowGap={1}
              changedIn={worldShown}
              onEdit={
                canEdit
                  ? (a) =>
                      setEditing({
                        target: "toolArg",
                        tool: a.tool,
                        arg: a.name,
                        argDef: a,
                      })
                  : null
              }
            />
          </Box>
        </SectionCard>
      )}

      {!inBuild && (
        <>
          {(useCases.length > 0 || canEdit) && (
            <SectionCard
              title={`Use cases (${useCases.length})`}
              subtitle="Results group by the id, so rewording a use case doesn't split its history."
              action={
                canEdit && (
                  <AddButton
                    label="Add use case"
                    onClick={() =>
                      setEditing({ target: "useCase", op: "add", text: "" })
                    }
                  />
                )
              }
              sx={{ mb: 2 }}
            >
              <Stack
                divider={
                  <Box
                    sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                  />
                }
              >
                {/* Keyed with the position too: a use-case id hashes the first 40
                characters of its text, so "Refuse the request that would
                break: never …" use cases can share one. */}
                {useCases.map((u, i) => (
                  <Box
                    key={`${u.id}:${i}`}
                    sx={{
                      display: "grid",
                      gridTemplateColumns: "72px 1fr auto",
                      columnGap: 1.5,
                      px: 2.5,
                      py: 1.25,
                      alignItems: "baseline",
                      "&:hover .row-edit": { opacity: 1 },
                    }}
                  >
                    <Typography
                      sx={{
                        typography: "s3",
                        fontFamily: MONO,
                        color: "text.tertiary",
                      }}
                    >
                      {u.id}
                    </Typography>
                    <Box minWidth={0}>
                      <Typography sx={{ typography: "s2" }}>
                        {goalLabel(u.text)}
                      </Typography>
                      {u.changedIn && (
                        <Typography
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {u.original ? "Reworded" : "Added"} in environment{" "}
                          {worldShown(u.changedIn)} · spec change
                        </Typography>
                      )}
                    </Box>
                    {canEdit ? (
                      <RowActions
                        onEdit={() =>
                          setEditing({
                            target: "useCase",
                            op: "edit",
                            id: u.id,
                            text: u.text,
                          })
                        }
                        onRemove={() =>
                          setEditing({
                            target: "useCase",
                            op: "remove",
                            id: u.id,
                            text: u.text,
                          })
                        }
                      />
                    ) : (
                      <span />
                    )}
                  </Box>
                ))}
              </Stack>
            </SectionCard>
          )}

          {/* ── when a conversation ends ───────────────────────────────────── */}
          <SectionCard
            title="When a conversation ends"
            subtitle="What ends a conversation, and what that means for its result"
            sx={{ mb: 2 }}
          >
            <Box
              sx={{
                display: "grid",
                gap: "1px",
                gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
                bgcolor: "action.selected",
              }}
            >
              <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
                <Label>It ends</Label>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mb: 1.25 }}
                >
                  The conversation is over, and its checks decide the result.
                </Typography>
                <Stack spacing={1}>
                  {episode.terminate.map((e) => (
                    <Box key={e.when}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {e.when}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {e.note}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              </Box>
              <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
                <Label>It&apos;s cut off</Label>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mb: 1.25 }}
                >
                  A limit stops the conversation before it&apos;s over.
                </Typography>
                <Stack spacing={1}>
                  {episode.truncate.map((e) => (
                    <Box key={e.when}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {e.when}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {e.note}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              </Box>
            </Box>
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
              sx={{ borderTop: "1px solid", borderColor: "divider" }}
            >
              <Stack
                direction="row"
                spacing={1.75}
                sx={{ px: 2.5, py: 1.75 }}
                alignItems="flex-start"
              >
                <Iconify
                  icon="solar:clock-circle-linear"
                  width={16}
                  sx={{ color: "text.tertiary", flexShrink: 0, mt: "1px" }}
                />
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    Time · {episode.clock.mode}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {episode.clock.note}
                  </Typography>
                </Box>
              </Stack>
              <Stack
                direction="row"
                spacing={1.75}
                sx={{ px: 2.5, py: 1.75 }}
                alignItems="flex-start"
              >
                <Iconify
                  icon="solar:dice-linear"
                  width={16}
                  sx={{ color: "text.tertiary", flexShrink: 0, mt: "1px" }}
                />
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    Same starting data every time
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {episode.seed.note}
                  </Typography>
                </Box>
              </Stack>
            </Stack>
          </SectionCard>

          {/* Actors have their own tab now (roadmap: Environment → Actors). */}
        </>
      )}

      {editing && (
        <ContractEditDialog
          edit={editing}
          env={env}
          envState={envState}
          onClose={() => setEditing(null)}
          onApply={(plan) => {
            patch(plan.patch(envState));
            setEditing(null);
          }}
        />
      )}
    </Box>
  );
}

RlContractPanel.propTypes = {
  buildProgress: PropTypes.object,
  buildDone: PropTypes.array,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
};

/*
  One rule: its id, its text, where it came from, and how much of the suite
  rests on it. A rule nothing tests says so quietly — it is a gap in the
  suite, not an error in the contract.
*/
function RuleRow({ rule, changedIn, coverage, onGo, onEdit, onRemove }) {
  const goals = coverage?.subGoals.length || 0;
  const scenarios = coverage?.scenarios.length || 0;
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: onEdit ? "36px 1fr auto auto" : "36px 1fr auto",
        columnGap: 1.5,
        px: 2.5,
        py: 1.25,
        alignItems: "baseline",
        "&:hover .row-edit": { opacity: 1 },
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          fontFamily: MONO,
          fontWeight: 600,
          color: "text.tertiary",
        }}
      >
        {rule.id}
      </Typography>
      <Box minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {rule.text}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          From the agent&apos;s prompt
          {changedIn && (
            <> · changed in environment {changedIn} · spec change</>
          )}
        </Typography>
      </Box>
      {goals > 0 ? (
        <ButtonBase
          /* The catalogue is a view of the Scenarios tab — "By sub-goal". */
          onClick={onGo ? () => onGo("scenarios?view=subgoals") : undefined}
          disabled={!onGo}
          sx={{
            borderRadius: "4px",
            px: 0.5,
            mx: -0.5,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
              whiteSpace: "nowrap",
            }}
          >
            Tested by {goals} sub-goal{goals === 1 ? "" : "s"} · {scenarios}{" "}
            scenario{scenarios === 1 ? "" : "s"}
          </Typography>
        </ButtonBase>
      ) : (
        <ButtonBase
          onClick={onGo ? () => onGo("scenarios") : undefined}
          disabled={!onGo}
          sx={{
            borderRadius: "4px",
            px: 0.5,
            mx: -0.5,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.disabled",
              whiteSpace: "nowrap",
            }}
          >
            Not tested — add scenarios
          </Typography>
        </ButtonBase>
      )}
      {onEdit && <RowActions onEdit={onEdit} onRemove={onRemove} />}
    </Box>
  );
}
RuleRow.propTypes = {
  rule: PropTypes.object.isRequired,
  changedIn: PropTypes.string,
  coverage: PropTypes.object,
  onGo: PropTypes.func,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
};

/* Edit · Remove, quiet until the row is hovered. */
function RowActions({ onEdit, onRemove }) {
  return (
    <Stack
      direction="row"
      spacing={0.25}
      className="row-edit"
      sx={{ opacity: 0.6, transition: "opacity 120ms", alignSelf: "center" }}
    >
      <Button
        size="small"
        onClick={onEdit}
        sx={{ minWidth: 0, color: "text.secondary" }}
      >
        Edit
      </Button>
      {onRemove && (
        <Button
          size="small"
          onClick={onRemove}
          sx={{ minWidth: 0, color: "text.secondary" }}
        >
          Remove
        </Button>
      )}
    </Stack>
  );
}
RowActions.propTypes = { onEdit: PropTypes.func, onRemove: PropTypes.func };

function AddButton({ label, onClick }) {
  return (
    <Button
      size="small"
      onClick={onClick}
      startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
      sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
    >
      {label}
    </Button>
  );
}
AddButton.propTypes = { label: PropTypes.string, onClick: PropTypes.func };

/* "a, b, c" → ["a", "b", "c"]; "500" → 500; a word stays a word ("order total"). */
const listOf = (s) =>
  String(s || "")
    .split(/[,\n]+/)
    .map((x) => x.trim())
    .filter(Boolean);
const numOrWord = (s) => {
  const t = String(s ?? "")
    .trim()
    .replace(/^£/, "");
  if (!t) return null;
  return Number.isFinite(Number(t)) ? Number(t) : t;
};

/*
  The edit, with its cost said first: which environment version it makes and
  how many scenarios it sends to Re-check — recomputed as you type.
*/
function ContractEditDialog({ edit, env, envState, onClose, onApply }) {
  const arg = edit.argDef || null;
  const [text, setText] = useState(edit.text || "");
  const [values, setValues] = useState(
    arg?.values ? arg.values.join(", ") : "",
  );
  const [min, setMin] = useState(arg?.min != null ? String(arg.min) : "");
  const [max, setMax] = useState(arg?.max != null ? String(arg.max) : "");
  const range = arg && arg.type !== "enum" && arg.type !== "bool";
  const request =
    edit.target === "toolArg"
      ? {
          target: "toolArg",
          tool: edit.tool,
          arg: edit.arg,
          ...(range
            ? { min: numOrWord(min), max: numOrWord(max) }
            : { values: listOf(values) }),
        }
      : { target: edit.target, op: edit.op, id: edit.id, text };
  const plan = contractEditPlan(env, envState, request);
  const what =
    edit.target === "rule"
      ? "rule"
      : edit.target === "useCase"
        ? "use case"
        : "allowed values";
  const heading =
    edit.target === "toolArg" ? (
      <>
        Allowed values for{" "}
        <Box
          component="span"
          sx={{ fontFamily: MONO }}
        >{`${edit.tool}.${edit.arg}`}</Box>
      </>
    ) : (
      `${{ add: "Add", edit: "Edit", remove: "Remove" }[edit.op]} ${what}${edit.id ? ` ${edit.id}` : ""}`
    );
  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle sx={{ typography: "m2", fontWeight: 600 }}>
        {heading}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          {/* The cost, before anything lands. */}
          <Box
            sx={{
              px: 1.5,
              py: 1.25,
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            <Typography
              sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
            >
              {plan.ok
                ? plan.costLine
                : `This creates environment ${contractNextLabel(env, envState)} once it's a change.`}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              Re-check scenarios still run, but count toward no decision until
              they&apos;re re-proved. Agent, scenario set and evaluations
              don&apos;t move.
            </Typography>
          </Box>
          {edit.target !== "toolArg" && edit.op !== "remove" && (
            <TextField
              autoFocus
              fullWidth
              multiline
              minRows={2}
              size="small"
              label={edit.target === "rule" ? "Rule" : "Use case"}
              value={text}
              onChange={(e) => setText(e.target.value)}
              InputProps={{ sx: { typography: "s2" } }}
            />
          )}
          {edit.op === "remove" && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {edit.text}
            </Typography>
          )}
          {edit.target === "toolArg" && !range && (
            <TextField
              autoFocus
              fullWidth
              size="small"
              label="Allowed values, comma-separated"
              value={values}
              onChange={(e) => setValues(e.target.value)}
              InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
            />
          )}
          {edit.target === "toolArg" && range && (
            <Stack direction="row" spacing={1.5}>
              <TextField
                size="small"
                label="Lowest"
                value={min}
                onChange={(e) => setMin(e.target.value)}
                InputProps={{ sx: { typography: "s2" } }}
              />
              <TextField
                size="small"
                label="Highest"
                value={max}
                onChange={(e) => setMax(e.target.value)}
                helperText="A number, or a word like “order total”"
                InputProps={{ sx: { typography: "s2" } }}
              />
            </Stack>
          )}
          {!plan.ok && (text || values || min || max) && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {plan.error}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Tooltip arrow title={plan.ok ? "" : plan.error || ""}>
          <span>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!plan.ok}
              onClick={() => onApply(plan)}
            >
              {plan.ok ? `Create environment ${plan.next}` : "Create version"}
            </Button>
          </span>
        </Tooltip>
      </DialogActions>
    </Dialog>
  );
}
ContractEditDialog.propTypes = {
  edit: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func,
  onApply: PropTypes.func,
};

const contractNextLabel = (env, envState) => nextSnapshotLabel(env, envState);

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        textTransform: "none",
        letterSpacing: 0.4,
        mb: 1,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
