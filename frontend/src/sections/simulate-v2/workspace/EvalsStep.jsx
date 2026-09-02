import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";
import { getEval, EVAL_CATALOG } from "../_mock/evals";
import { useAppliedEvals, EvalRow } from "./evals/appliedEvals";
import AddEvalsDrawer from "./evals/AddEvalsDrawer";
import TwinEvalEditor from "./evals/TwinEvalEditor";
import ReleaseGate from "./ReleaseGate";

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
export default function EvalsStep({ env, envState, patch, onGo, buildMode }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [twinEditorOpen, setTwinEditorOpen] = useState(false);
  const twinBacked = !!envState?.twinBacking;
  // The workspace routes away from this step when there are no scenarios;
  // this is the backstop if it is ever rendered directly.
  const needsScenarios = (envState?.scenarios?.length || 0) === 0;

  const { appliedEvals, appliedIds, add, remove } = useAppliedEvals(envState, patch);

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
              <Tooltip arrow title="Author a structured assertion against final clone state — deterministic, not a judge prompt.">
                <span>
                  <Button
                    variant="outlined"
                    size="small"
                    disabled={needsScenarios}
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
            <Tooltip arrow title={needsScenarios ? "Add scenarios first" : ""}>
              <span>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={needsScenarios}
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

      {/*
        Suggested → Added split.
        Adding a suggestion moves it into Added below and it disappears
        from this list; removing it from Added returns it here.
      */}
      {suggested.length > 0 && !needsScenarios && (
        <SectionCard
          title={`Suggested evaluations (${suggested.length})`}
          subtitle="The environment thinks these would matter — add the ones you want the run scored against."
          sx={{ mb: 2 }}
          action={
            <Button
              size="small"
              onClick={() => add(suggested)}
              sx={{ typography: "s2", fontWeight: 700, color: "primary.main" }}
            >
              Add all {suggested.length}
            </Button>
          }
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {suggested.map((e) => (
              <EvalRow
                key={e.id}
                item={e}
                action={
                  <Button
                    size="small"
                    onClick={() => add([e])}
                    startIcon={<Iconify icon="solar:add-circle-linear" width={13} />}
                    sx={{ typography: "s2", fontWeight: 700, color: "primary.main", minWidth: 0 }}
                  >
                    Add
                  </Button>
                }
              />
            ))}
          </Stack>
        </SectionCard>
      )}

      {/* ── what will actually score the run ── */}
      <SectionCard
        title={`Added evaluations (${appliedEvals.length})`}
        subtitle={
          appliedEvals.length
            ? "Every task is scored against these. Add more from Suggested or the library any time."
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
                : suggested.length > 0
                  ? "Pick one from the suggestions above, or open the library for more."
                  : "You can run without them — you'll get traces, but nothing will tell you whether the agent was right."
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
                  /* No always-on evals. Every added row is removable. */
                  <IconButton size="small" onClick={() => remove(e.id)}>
                    <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                )}
              />
            ))}
          </Stack>
        )}
      </SectionCard>

      {/* Release gate — post-run only. */}
      {!buildMode && appliedEvals.length > 0 && (
        <Box sx={{ mt: 2 }}>
          <ReleaseGate envState={envState} patch={patch} />
        </Box>
      )}

      {/* Select many, map them one at a time. */}
      <AddEvalsDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        env={env}
        envState={envState}
        existingIds={appliedIds}
        onAdd={add}
      />

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
};
