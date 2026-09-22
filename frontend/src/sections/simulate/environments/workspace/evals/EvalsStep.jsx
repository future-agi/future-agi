import { useMemo, useState, useEffect, useRef } from "react";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { getEval } from "src/api/simulate-environments/_fixtures/evalCatalog";
import { useRemoveAppliedEvaluation } from "src/api/simulate-environments/environments";
import { BUILD_STATUS } from "../../myEnvironments.constants";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import EvalRow from "./EvalRow";
import AddEvalsDrawer from "./AddEvalsDrawer";
import { useAppliedEvals } from "./useAppliedEvals";
import { EVALS_COPY, ENV_SHAPE, ENV_STATE_SHAPE } from "./evals.constants";
import PropTypes from "prop-types";

// A seeded-from-template env is read-only until forked; every add/remove control
// carries this on its tooltip while locked.
const LOCK_TOOLTIP = "Fork this environment to edit.";

/**
 * Evaluations tab.
 *
 * The environment's preset evals are auto-seeded into Added on first empty
 * mount — nobody ever wanted the suggestions to *not* be scored, so a separate
 * "Suggested" card the user had to click "Add all" on was a formality. The
 * seed is ref-guarded, so deliberately clearing everything doesn't re-add them;
 * the user removes any of them from Added or opens the library for more.
 * "Add evaluations" opens the product's eval picker for the rest of the library.
 *
 * The designer's twin-backed suggestions and clone-eval editor are out of
 * scope for this phase and are not ported.
 */
export default function EvalsStep({ env, envState, patch, onGo, locked = false, backed = false }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  // The workspace routes away from this tab when there are no scenarios; this
  // is the backstop if it is ever rendered directly without them.
  const needsScenarios = (envState?.scenarios?.length || 0) === 0;

  const { appliedEvals, appliedIds, add, remove } = useAppliedEvals(envState, patch);

  // §9 remove. On a real backend-backed env (`backed`), removal is a server
  // soft-delete of `evaluations.selected[].id`: the store row is dropped only
  // once the DELETE succeeds, and the detail query is invalidated so the real
  // selected set is the source of truth. Disabled while building (409). A
  // forked/template env has no backend counterpart, so it stays store-only.
  // There is no add endpoint (§9), so "Add evaluations" is client-side either way.
  //
  // NB: the row `id` is a real `eval_config_id` only when the §6 detail read is
  // on (HARNESS_DETAIL_ENABLED); with it off the rows are fixture/preset-seeded,
  // so the DELETE fires with a fixture id and 404s until §6 is also enabled.
  const removeEval = useRemoveAppliedEvaluation();
  const building = env.buildStatus === BUILD_STATUS.BUILDING;
  const removeDisabled = locked || (backed && (building || removeEval.isPending));
  const onRemove = (id) => {
    if (!backed) {
      remove(id);
      return;
    }
    removeEval.mutate({ id: env.id, evalConfigId: id }, { onSuccess: () => remove(id) });
  };

  // The env's preset evals, minus anything already added — the set that gets
  // auto-seeded into Added, and the reason the empty state may still appear
  // (a preset with nothing left to seed).
  const suggested = useMemo(() => {
    const presetIds = env.evalPreset || [];
    return presetIds
      .map(getEval)
      .filter(Boolean)
      .filter((e) => !appliedIds.has(e.id))
      .filter((e, i, arr) => arr.findIndex((x) => x.id === e.id) === i);
  }, [env.evalPreset, appliedIds]);

  // Auto-seed the preset into Added on first empty mount. Ref-guarded so a
  // deliberate "remove all" doesn't loop the suggestions straight back in.
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
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ mb: 3 }}
      >
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            {EVALS_COPY.heading}
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 720 }}>
            {EVALS_COPY.intro}
          </Typography>
        </Box>
        {/*
          The header CTA only appears once Added has something in it. While it's
          empty the empty-state card below already offers a prominent Add button
          — two CTAs for one action read as noise.
        */}
        {appliedEvals.length > 0 && (
          <Tooltip arrow title={locked ? LOCK_TOOLTIP : needsScenarios ? EVALS_COPY.needsScenariosHint : ""}>
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled={needsScenarios || locked}
                onClick={() => setPickerOpen(true)}
                startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                {EVALS_COPY.add}
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>

      <SectionCard
        title={EVALS_COPY.addedTitle(appliedEvals.length)}
        subtitle={appliedEvals.length ? EVALS_COPY.addedSubtitle : undefined}
      >
        {appliedEvals.length === 0 ? (
          <EmptyState
            icon={
              needsScenarios
                ? "solar:lock-keyhole-minimalistic-linear"
                : "solar:shield-check-linear"
            }
            title={needsScenarios ? EVALS_COPY.lockedTitle : EVALS_COPY.emptyTitle}
            body={needsScenarios ? EVALS_COPY.lockedBody : EVALS_COPY.emptyNoSuggestions}
            action={
              // The needsScenarios branch is a nav to the Scenarios tab, not a
              // mutation, so it stays live even on a locked template; only the
              // "add evaluations" branch is gated behind a fork.
              <Tooltip arrow title={locked && !needsScenarios ? LOCK_TOOLTIP : ""}>
                <Box component="span" sx={{ display: "inline-flex" }}>
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    disabled={locked && !needsScenarios}
                    onClick={() => (needsScenarios ? onGo?.("scenarios") : setPickerOpen(true))}
                    endIcon={
                      needsScenarios ? <Iconify icon="solar:arrow-right-linear" width={15} /> : null
                    }
                    sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
                  >
                    {needsScenarios ? EVALS_COPY.addScenarios : EVALS_COPY.add}
                  </Button>
                </Box>
              </Tooltip>
            }
          />
        ) : (
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {appliedEvals.map((e) => (
              <EvalRow
                key={e.id}
                item={e}
                action={
                  // No always-on evals. Every added row is removable — except on
                  // a locked template, where every edit is gated behind a fork.
                  <Tooltip
                    arrow
                    title={
                      locked
                        ? LOCK_TOOLTIP
                        : backed && building
                          ? "Available once the environment finishes building."
                          : ""
                    }
                  >
                    <Box component="span" sx={{ display: "inline-flex" }}>
                      <IconButton size="small" disabled={removeDisabled} aria-label={EVALS_COPY.remove} onClick={() => onRemove(e.id)}>
                        <Iconify
                          icon="solar:trash-bin-trash-linear"
                          width={16}
                          sx={{ color: "text.subtitle" }}
                        />
                      </IconButton>
                    </Box>
                  </Tooltip>
                }
              />
            ))}
          </Stack>
        )}
      </SectionCard>

      <AddEvalsDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        env={env}
        envState={envState}
        existingIds={appliedIds}
        onAdd={add}
      />
    </Box>
  );
}

EvalsStep.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
  backed: PropTypes.bool,
};
