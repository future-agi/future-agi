import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Stack, Typography, Button, IconButton, Tooltip, Switch } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";
import { getEval, EVAL_CATALOG } from "../_mock/evals";
import { useAppliedEvals, EvalRow } from "./evals/appliedEvals";
import AddEvalsDrawer from "./evals/AddEvalsDrawer";
import TwinEvalEditor from "./evals/TwinEvalEditor";
import EditEvalDrawer, { canEditEval } from "./evals/EditEvalDrawer";

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
export default function EvalsStep({ env, envState, patch, onGo, locked = false, onFork }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [twinEditorOpen, setTwinEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const twinBacked = !!envState?.twinBacking;
  // The workspace routes away from this step when there are no scenarios;
  // this is the backstop if it is ever rendered directly.
  const needsScenarios = (envState?.scenarios?.length || 0) === 0;

  const { appliedEvals, appliedIds, add, remove, update } = useAppliedEvals(envState, patch);
  const editing = appliedEvals.find((e) => e.id === editingId) || null;

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
      ? EVAL_CATALOG.filter((e) => e.evalKind === "twin_end_state").map((e) => e.id)
      : [];
    const orderedIds = [...presetIds, ...twinIds];
    return orderedIds
      .map(getEval)
      .filter(Boolean)
      .filter((e) => !appliedIds.has(e.id))
      /* De-duplicate — a twin id could theoretically also live in preset. */
      .filter((e, i, arr) => arr.findIndex((x) => x.id === e.id) === i);
  }, [env.evalPreset, envState?.twinBacking, appliedIds]);

  /*
    Auto-seed the env's suggested evals into Added on first empty mount.
    The old flow had a separate "Suggested" card the user had to click
    "Add all" on — but nobody ever wanted the suggestions to *not* be
    scored, so the click was a formality. Preseed on empty and drop the
    Suggested card entirely; the user can still remove any of them from
    Added or open the library for more. Ref-guarded so we don't loop
    when the user deliberately clears everything.
    */
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current) return;
    if (needsScenarios) return;
    if (appliedEvals.length > 0) { seededRef.current = true; return; }
    if (suggested.length === 0) { seededRef.current = true; return; }
    seededRef.current = true;
    add(suggested);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsScenarios, suggested.length, appliedEvals.length]);


  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 3 }}>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Evaluations</Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 720 }}>
            These decide whether each task passed. Pick them from the library and map
            their inputs onto what the run produces.
          </Typography>
        </Box>
        {/*
          Only show the header CTA once the "Added evaluations" list has
          something in it. When it's empty, the empty-state card below
          already renders a prominent Add evaluations button — two CTAs
          for the same action read as noise.
        */}
        {appliedEvals.length > 0 && (
          <Stack direction="row" spacing={1}>
            {twinBacked && (
              <Tooltip arrow title={locked ? "Fork this environment to author evals." : "Author a structured assertion against final clone state — deterministic, not a judge prompt."}>
                <span>
                  <Button
                    variant="outlined"
                    size="small"
                    disabled={needsScenarios || locked}
                    onClick={() => setTwinEditorOpen(true)}
                    startIcon={<Iconify icon="solar:server-square-linear" width={14} />}
                    sx={{
                      typography: "s2", fontWeight: 700,
                      color: "text.primary",
                      borderColor: "divider",
                      "&:hover": { borderColor: "text.disabled" },
                    }}
                  >
                    Author clone eval
                  </Button>
                </span>
              </Tooltip>
            )}
            <Tooltip arrow title={locked ? "Fork this environment to add evaluations." : needsScenarios ? "Add scenarios first" : ""}>
              <span>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={needsScenarios || locked}
                  onClick={() => setPickerOpen(true)}
                  startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  Add evaluations
                </Button>
              </span>
            </Tooltip>
          </Stack>
        )}
      </Stack>

      {/* Tool-call evaluation — carried over from the legacy run setup. Tool
          calls are read from the connected agent, so it needs one first. */}
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 2, py: 1.5, mb: 2, borderRadius: 1, border: "1px solid", borderColor: "divider" }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>Enable tool call evaluation</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>
            {envState?.agent
              ? "Tool calling that happens during the calls will be evaluated — the right tool, with the right arguments, at the right time."
              : "Connect your agent first — tool calls are read from it during the run."}
          </Typography>
        </Box>
        <Tooltip arrow title={locked ? "Fork this environment to edit." : !envState?.agent ? "Connect an agent to evaluate its tool calls." : ""}>
          <span>
            <Switch
              checked={!!envState?.toolCallEval && !!envState?.agent}
              disabled={locked || !envState?.agent}
              onChange={(e) => patch({ toolCallEval: e.target.checked })}
              inputProps={{ "aria-label": "Enable tool call evaluation" }}
            />
          </span>
        </Tooltip>
      </Stack>

      {/* ── what will actually score the run ── */}
      <SectionCard
        title={`Added evaluations (${appliedEvals.length})`}
        subtitle={
          appliedEvals.length
            ? "Every task is scored against these. Add more from the library any time."
            : undefined
        }
      >
        {appliedEvals.length === 0 ? (
          <EmptyState
            icon={needsScenarios ? "solar:lock-keyhole-minimalistic-linear" : "solar:shield-check-linear"}
            title={needsScenarios ? "Add scenarios first" : "No evaluations added yet"}
            body={
              needsScenarios
                ? "An evaluation scores the tasks a run produces, so it needs scenarios to point at. Add some and this unlocks."
                : "You can run without them — you'll get traces, but nothing will tell you whether the agent was right. Open the library to add some."
            }
            action={
              <Stack direction="row" spacing={1}>
                {twinBacked && !needsScenarios && (
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={() => setTwinEditorOpen(true)}
                    startIcon={<Iconify icon="solar:server-square-linear" width={14} />}
                    sx={{
                      typography: "s2", fontWeight: 700,
                      color: "text.primary",
                      borderColor: "divider",
                      "&:hover": { borderColor: "text.disabled" },
                    }}
                  >
                    Author clone eval
                  </Button>
                )}
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={() => (needsScenarios ? onGo("scenarios") : setPickerOpen(true))}
                  endIcon={needsScenarios ? <Iconify icon="solar:arrow-right-linear" width={15} /> : null}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  {needsScenarios ? "Add scenarios" : "Add evaluations"}
                </Button>
              </Stack>
            }
          />
        ) : (
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {appliedEvals.map((e) => (
              <EvalRow
                key={e.id}
                item={e}
                action={(
                  /* No always-on evals. Every added row is editable and
                     removable — except on a locked template, where every
                     edit is gated behind a fork. */
                  <Stack direction="row" spacing={0.25}>
                    <Tooltip
                      arrow
                      title={locked ? "Fork this environment to edit."
                        : canEditEval(e) ? "Edit" : "Clone-state evals are authored assertions — remove and re-author to change one."}
                    >
                      <span>
                        <IconButton size="small" disabled={locked || !canEditEval(e)} onClick={() => setEditingId(e.id)} aria-label={`Edit ${e.name}`}>
                          <Iconify icon="solar:pen-linear" width={16} sx={{ color: "text.subtitle" }} />
                        </IconButton>
                      </span>
                    </Tooltip>
                    <Tooltip arrow title={locked ? "Fork this environment to edit." : "Remove"}>
                      <span>
                        <IconButton size="small" disabled={locked} onClick={() => remove(e.id)} aria-label={`Remove ${e.name}`}>
                          <Iconify icon="solar:trash-bin-trash-linear" width={16} sx={{ color: "text.subtitle" }} />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </Stack>
                )}
              />
            ))}
          </Stack>
        )}
      </SectionCard>

      {/* Select many, map them one at a time. */}
      <AddEvalsDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        env={env}
        envState={envState}
        existingIds={appliedIds}
        onAdd={add}
      />

      {editing && (
        <EditEvalDrawer
          item={editing}
          env={env}
          envState={envState}
          onClose={() => setEditingId(null)}
          onSave={(changes) => update(editing.id, changes)}
        />
      )}

      {twinBacked && (
        <TwinEvalEditor
          open={twinEditorOpen}
          envState={envState}
          onClose={() => setTwinEditorOpen(false)}
          onSave={(evalItem) => { add([evalItem]); setTwinEditorOpen(false); }}
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
};
