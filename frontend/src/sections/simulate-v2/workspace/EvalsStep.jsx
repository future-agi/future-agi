import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tooltip,
  Switch,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";
import {
  getEval,
  EVAL_CATALOG,
  EVAL_CAP_NOTE,
  MAX_APPLIED_EVALS,
  REQUIRED_EVAL_NOTE,
  TOOL_CALL_JUDGE_ID,
  cappedEvalCount,
} from "../_mock/evals";
import { EvalRow } from "./evals/appliedEvals";
import { useAppliedEvals } from "./evals/useAppliedEvals";
import AddEvalsDrawer from "./evals/AddEvalsDrawer";
import TwinEvalEditor from "./evals/TwinEvalEditor";
import EditEvalDrawer from "./evals/EditEvalDrawer";
import { canEditEval } from "./evals/canEditEval";
import { worldToolsFor } from "../_mock/toolFit";
import { runScopeOf } from "../_mock/runScope";
import { evalCreditsPerRun, evalRailOf } from "../_mock/metering";

/* The catalogue eval behind the "tool call evaluation" switch. */
const TOOL_CALL_EVAL_ID = TOOL_CALL_JUDGE_ID;

/**
 * Evals.
 *
 * Adding evals opens a drawer over the real eval library: tick as many as you
 * want, then map them one at a time behind a completion bar. The shared
 * EvalPickerDrawer handles one eval per trip, which means four evals is four
 * passes through the same list.
 *
 * Above it sits a short recommended strip: the environment knows which evals
 * matter for it, so the fastest correct set is one click, and the drawer is
 * there for everything else.
 */
export default function EvalsStep({
  env,
  envState,
  patch,
  onGo,
  locked = false,
  onFork,
  repeats = 3,
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [twinEditorOpen, setTwinEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const twinBacked = !!envState?.twinBacking;
  // The workspace routes away from this step when there are no scenarios;
  // this is the backstop if it is ever rendered directly.
  const needsScenarios = (envState?.scenarios?.length || 0) === 0;

  const {
    appliedEvals,
    appliedIds,
    add,
    remove,
    update,
    setRequired,
    slotsLeft,
  } = useAppliedEvals(envState, patch);
  /* Up to 8 catalogue evaluations plus the tool-call judge. */
  const atCap = slotsLeft <= 0;
  const cappedCount = cappedEvalCount(envState?.evals || []);
  const requiredCount = appliedEvals.filter((e) => e.required).length;
  const editing = appliedEvals.find((e) => e.id === editingId) || null;

  const hasTools = worldToolsFor(env, envState).length > 0;

  /* What the evals cost per run (AC-16.4): managed judges meter AI credits
     per call; rule-based and own-key evals are free. Counted over the
     scenarios a run would actually take — the same plan the Run button
     reads. */
  const runnable = useMemo(
    /* Less what sits out on an open question — the count the Run button uses. */
    () => runScopeOf(env, envState).count,
    [env, envState],
  );
  const creditsPerRun = evalCreditsPerRun({
    evals: appliedEvals,
    scenarios: runnable,
    repeats,
  });
  const meteredCount = appliedEvals.filter((e) => evalRailOf(e).metered).length;
  const toolCallEval = getEval(TOOL_CALL_EVAL_ID);
  const toolCallEvalOn = appliedIds.has(TOOL_CALL_EVAL_ID);

  /*
    Suggested = the environment's preset MINUS anything already added.
    Adding one moves it into `envState.evals` (Added below) and it
    vanishes from here; removing it from Added returns it to this list
    automatically, because Suggested is derived, not stored.
  */
  /*
    Suggestion set has two sources:
      · The env's preset (the built-in list per surface).
      · Twin-state evals, added only when the env is twin-backed.
        This is the wedge — twin-backed envs get "was the DM in the
        right channel", "no unexpected writes", etc. as first-class
        suggestions the moment the env exists, not something the user
        has to hunt down in the picker.
  */
  const suggested = useMemo(() => {
    const presetIds = env.evalPreset || [];
    const twinIds = envState?.twinBacking
      ? EVAL_CATALOG.filter((e) => e.evalKind === "twin_end_state").map(
          (e) => e.id,
        )
      : [];
    const orderedIds = [...presetIds, ...twinIds];
    return (
      orderedIds
        .map(getEval)
        .filter(Boolean)
        .filter((e) => !appliedIds.has(e.id))
        /* De-duplicate — a twin id could theoretically also live in preset. */
        .filter((e, i, arr) => arr.findIndex((x) => x.id === e.id) === i)
    );
  }, [env.evalPreset, envState?.twinBacking, appliedIds]);

  /*
    Auto-seed the env's suggested evals into Added on first empty mount.
    The old flow had a separate "Suggested" card the user had to click
    "Add all" on — but nobody ever wanted the suggestions to *not* be
    scored, so the click was a formality. Preseed on empty and drop the
    Suggested card entirely; the user can still remove any of them from
    Added or open the library for more. Once per environment
    (`evalsSeeded`), so clearing them stays cleared — and the run says
    it needs one.
    */
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current || envState?.evalsSeeded) return;
    if (needsScenarios) return;
    if (appliedEvals.length > 0) {
      seededRef.current = true;
      return;
    }
    if (suggested.length === 0) {
      seededRef.current = true;
      return;
    }
    seededRef.current = true;
    add(suggested);
    patch({ evalsSeeded: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsScenarios, suggested.length, appliedEvals.length]);

  return (
    <Box sx={{ p: 2 }}>
      {/* No heading or description — the tab names the page and its hover
          hint carries the description. Add evaluations sits on the card it
          adds to. */}
      {/* Tool-call evaluation — carried over from the legacy run setup. It is
          the catalogue's tool-correctness eval, added to and removed from the
          applied list like any other, so the switch and the list below can
          never disagree. It needs tools for the world to answer, not an agent. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2,
          py: 1.5,
          mb: 2,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            Enable tool call evaluation
          </Typography>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
          >
            {hasTools
              ? "Tool calling that happens during the calls will be evaluated — the right tool, with the right arguments, at the right time."
              : "This environment has no tools yet — there are no tool calls to evaluate."}
          </Typography>
        </Box>
        <Tooltip
          arrow
          title={
            locked
              ? "Fork this environment to edit."
              : !hasTools
                ? "Add tools to the world to evaluate tool calls."
                : ""
          }
        >
          <span>
            <Switch
              checked={toolCallEvalOn}
              disabled={locked || !hasTools || !toolCallEval}
              onChange={(e) =>
                e.target.checked
                  ? add([toolCallEval])
                  : remove(TOOL_CALL_EVAL_ID)
              }
              inputProps={{ "aria-label": "Enable tool call evaluation" }}
            />
          </span>
        </Tooltip>
      </Stack>

      {/* ── what will actually score the run ── */}
      <SectionCard
        title={`Added evaluations (${appliedEvals.length})`}
        /*
          Only once the list has something in it. When it's empty, the
          empty-state below already renders a prominent Add evaluations
          button — two CTAs for the same action read as noise.
        */
        action={
          appliedEvals.length > 0 && (
            <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
              {twinBacked && (
                <Tooltip
                  arrow
                  title={
                    locked
                      ? "Fork this environment to author evals."
                      : "Write a check against the world's end state — deterministic, not a judge prompt."
                  }
                >
                  <span>
                    <Button
                      variant="outlined"
                      size="small"
                      disabled={needsScenarios || locked}
                      onClick={() => setTwinEditorOpen(true)}
                      startIcon={
                        <Iconify icon="solar:server-square-linear" width={14} />
                      }
                      sx={{
                        color: "text.primary",
                        borderColor: "divider",
                        "&:hover": { borderColor: "text.disabled" },
                      }}
                    >
                      Author sandbox eval
                    </Button>
                  </span>
                </Tooltip>
              )}
              <Tooltip
                arrow
                title={
                  locked
                    ? "Fork this environment to add evaluations."
                    : needsScenarios
                      ? "Add scenarios first"
                      : atCap
                        ? EVAL_CAP_NOTE
                        : ""
                }
              >
                <span>
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    disabled={needsScenarios || locked || atCap}
                    onClick={() => setPickerOpen(true)}
                    startIcon={
                      <Iconify icon="solar:add-circle-linear" width={16} />
                    }
                  >
                    Add evaluations
                  </Button>
                </span>
              </Tooltip>
            </Stack>
          )
        }
        subtitle={
          appliedEvals.length
            ? `Every task is scored against these · ${cappedCount} of ${MAX_APPLIED_EVALS}${toolCallEvalOn ? " plus the tool-call judge" : ""}${requiredCount ? ` · ${requiredCount} required` : ""}. Sub-goals decide pass or fail; a required one also fails the conversation when it fails, the rest score it.`
            : undefined
        }
      >
        {appliedEvals.length === 0 ? (
          <EmptyState
            icon={
              needsScenarios
                ? "solar:lock-keyhole-minimalistic-linear"
                : "solar:shield-check-linear"
            }
            title={
              needsScenarios
                ? "Add scenarios first"
                : "No evaluations added yet"
            }
            body={
              needsScenarios
                ? "An evaluation scores the tasks a run produces, so it needs scenarios to point at. Add some and this unlocks."
                : "Optional — sub-goals decide pass or fail without them. Add evaluations to score each conversation, and mark one required to let it fail one. Open the library to add some."
            }
            action={
              <Stack direction="row" spacing={1}>
                {twinBacked && !needsScenarios && (
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={() => setTwinEditorOpen(true)}
                    startIcon={
                      <Iconify icon="solar:server-square-linear" width={14} />
                    }
                    sx={{
                      color: "text.primary",
                      borderColor: "divider",
                      "&:hover": { borderColor: "text.disabled" },
                    }}
                  >
                    Author sandbox eval
                  </Button>
                )}
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={() =>
                    needsScenarios ? onGo("scenarios") : setPickerOpen(true)
                  }
                  endIcon={
                    needsScenarios ? (
                      <Iconify icon="solar:arrow-right-linear" width={16} />
                    ) : null
                  }
                >
                  {needsScenarios ? "Add scenarios" : "Add evaluations"}
                </Button>
              </Stack>
            }
          />
        ) : (
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {appliedEvals.map((e) => (
              <EvalRow
                key={e.id}
                item={e}
                action={
                  /* No always-on evals. Every added row is editable and
                     removable — except on a locked template, where every
                     edit is gated behind a fork. */
                  <Stack direction="row" alignItems="center" spacing={0.25}>
                    {/* Required — a failing score fails the conversation. Stored
                        on the applied eval, so runs and an improvement's goal
                        picker read it from envState.evals. */}
                    <Tooltip
                      arrow
                      title={
                        locked
                          ? "Fork this environment to edit."
                          : `Required — ${REQUIRED_EVAL_NOTE}`
                      }
                    >
                      <Stack
                        component="label"
                        direction="row"
                        alignItems="center"
                        spacing={0.25}
                        sx={{
                          mr: 0.75,
                          cursor: locked ? "default" : "pointer",
                        }}
                      >
                        <Switch
                          size="small"
                          checked={!!e.required}
                          disabled={locked}
                          onChange={(ev) =>
                            setRequired(e.id, ev.target.checked)
                          }
                          inputProps={{ "aria-label": `Required: ${e.name}` }}
                        />
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 600,
                            color: e.required
                              ? "text.primary"
                              : "text.tertiary",
                          }}
                        >
                          Required
                        </Typography>
                      </Stack>
                    </Tooltip>
                    <Tooltip
                      arrow
                      title={
                        canEditEval(e)
                          ? "View mapping"
                          : "An authored assertion — it has no mapping to view."
                      }
                    >
                      <span>
                        {/* Viewing is never gated: a mapping is read-only, and removing it is the only change. */}
                        <IconButton
                          size="small"
                          disabled={!canEditEval(e)}
                          onClick={() => setEditingId(e.id)}
                          aria-label={`View mapping for ${e.name}`}
                        >
                          <Iconify
                            icon="solar:eye-linear"
                            width={16}
                            sx={{ color: "text.tertiary" }}
                          />
                        </IconButton>
                      </span>
                    </Tooltip>
                    <Tooltip
                      arrow
                      title={
                        locked ? "Fork this environment to edit." : "Remove"
                      }
                    >
                      <span>
                        <IconButton
                          size="small"
                          disabled={locked}
                          onClick={() => remove(e.id)}
                          aria-label={`Remove ${e.name}`}
                        >
                          <Iconify
                            icon="solar:trash-bin-trash-linear"
                            width={16}
                            sx={{ color: "text.tertiary" }}
                          />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </Stack>
                }
              />
            ))}
          </Stack>
        )}
      </SectionCard>

      {atCap && !locked && (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 1.25, px: 0.5 }}
        >
          {EVAL_CAP_NOTE}
        </Typography>
      )}

      {appliedEvals.length > 0 && (
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            mt: 1.25,
            px: 0.5,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {meteredCount > 0
            ? `This environment's evals cost about ${creditsPerRun.toLocaleString()} credits per run (${runnable} scenario${runnable === 1 ? "" : "s"} × ${repeats} repeat${repeats === 1 ? "" : "s"}).`
            : "This environment's evals are free — each one is rule-based or judged by your own model key."}
          {meteredCount > 0 && meteredCount < appliedEvals.length && (
            <Box component="span" sx={{ color: "text.tertiary" }}>
              {` ${appliedEvals.length - meteredCount} of ${appliedEvals.length} are free.`}
            </Box>
          )}
        </Typography>
      )}

      {/* Select many, map them one at a time. */}
      <AddEvalsDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        env={env}
        envState={envState}
        existingIds={appliedIds}
        onAdd={add}
        slotsLeft={slotsLeft}
      />

      {editing && (
        <EditEvalDrawer
          item={editing}
          env={env}
          envState={envState}
          locked={locked}
          onClose={() => setEditingId(null)}
          onSave={(changes) => update(editing.id, changes)}
        />
      )}

      {twinBacked && (
        <TwinEvalEditor
          open={twinEditorOpen}
          envState={envState}
          onClose={() => setTwinEditorOpen(false)}
          onSave={(evalItem) => {
            add([evalItem]);
            setTwinEditorOpen(false);
          }}
        />
      )}
    </Box>
  );
}

EvalsStep.propTypes = {
  buildMode: PropTypes.bool,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
  onFork: PropTypes.func,
  repeats: PropTypes.number,
};
