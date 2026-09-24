import { useMemo, useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Box, Stack, Typography, Button, IconButton, Tooltip, Switch } from "@mui/material";
import Iconify from "src/components/iconify";
import { getEval } from "src/api/simulate-environments/_fixtures/evalCatalog";
import { useRemoveAppliedEvaluation } from "src/api/simulate-environments/environments";
import { harnessEnvironmentQuery } from "src/api/simulate-environments/environment";
import { useToolCallEval } from "src/api/simulate-environments/toolCallEval";
import { BUILD_STATUS } from "../../myEnvironments.constants";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import EvalRow from "./EvalRow";
import SelectedEvalRow from "./SelectedEvalRow";
import AddEvalsDrawer from "./AddEvalsDrawer";
import AddEvaluationDrawer from "./AddEvaluationDrawer";
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

  const store = useAppliedEvals(envState, patch);
  // Env-level "enable tool call evaluation" — client state today, persisted
  // through a mocked seam until the backend endpoint exists (useToolCallEval).
  const toolCall = useToolCallEval();

  // A real backend-backed env drives its applied set from §5 detail
  // (evaluations.selected) — the authoritative list the add (§3) and remove
  // (§4) endpoints mutate. A forked/template env has no backend counterpart, so
  // it stays store-driven (fixture-seeded preset). The §5 query shares its cache
  // with useAddEvaluation's setQueryData, so an add/remove reflects immediately.
  const detailQuery = useQuery(harnessEnvironmentQuery(env.id, { enabled: backed }));
  // §5 selected[]: each row is a full §1 entry plus `id` and `runnable`. Kept
  // whole — the tab shows `source`, the cost line (`credits_per_run` /
  // `charges_judge_tokens`) and `inputs`, which the old {id,name,blurb}
  // projection threw away.
  const selectedFromDetail = useMemo(() => {
    const selected = detailQuery.data?.evaluations?.selected;
    return Array.isArray(selected) ? selected : [];
  }, [detailQuery.data]);

  const appliedEvals = backed ? selectedFromDetail : store.appliedEvals;
  const appliedIds = store.appliedIds;
  const { add } = store;

  // §4 remove. On a backed env, removal is a server soft-delete of
  // `evaluations.selected[].id`; the §5 query is invalidated so the real set is
  // the source of truth. Disabled while building (409). A forked/template env
  // stays store-only.
  const removeEval = useRemoveAppliedEvaluation();
  const building = env.buildStatus === BUILD_STATUS.BUILDING;
  const removeDisabled = locked || (backed && (building || removeEval.isPending));
  const onRemove = (id) => {
    if (!backed) {
      store.remove(id);
      return;
    }
    removeEval.mutate({ id: env.id, evalConfigId: id });
  };

  // With several rows, one Alert for the whole card doesn't say which one a
  // failed remove belongs to. `removeEval.variables` is the mutation's own
  // last input — `{ id, evalConfigId }` — so the failed row is whichever
  // applied eval still carries that config id.
  const failedRemoveName = useMemo(() => {
    if (!removeEval.isError) return null;
    // An absent `variables` (or one carrying no `evalConfigId`) must not
    // fall through to `e.id === undefined` — that would match the first
    // applied eval that happens to carry no `id` and attribute the Alert to
    // a row the refusal has nothing to do with. Not reachable through the
    // contract today; the guard is defensive.
    const failedId = removeEval.variables?.evalConfigId;
    if (!failedId) return null;
    return appliedEvals.find((e) => e.id === failedId)?.name || null;
  }, [removeEval.isError, removeEval.variables, appliedEvals]);

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
    // A backed env's applied set is real (§5) — never seed fixtures into it.
    if (backed) { seededRef.current = true; return; }
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

      {/* Tool-call evaluation. Client state for now; the value rides through the
          mocked useToolCallEval seam until an env-level API lands. Gated on a
          connected agent — tool calls are read from it during the run. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2, py: 1.5, mb: 3, borderRadius: 1, border: "1px solid", borderColor: "divider" }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
            {EVALS_COPY.toolCall.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>
            {envState?.agent ? EVALS_COPY.toolCall.on : EVALS_COPY.toolCall.off}
          </Typography>
        </Box>
        <Tooltip arrow title={locked ? LOCK_TOOLTIP : !envState?.agent ? EVALS_COPY.toolCall.needsAgent : ""}>
          <span>
            <Switch
              checked={!!envState?.toolCallEval && !!envState?.agent}
              disabled={locked || !envState?.agent}
              onChange={(e) => {
                const enabled = e.target.checked;
                patch({ toolCallEval: enabled });
                toolCall.mutate({ envId: env.id, enabled });
              }}
              inputProps={{ "aria-label": EVALS_COPY.toolCall.title }}
            />
          </span>
        </Tooltip>
      </Stack>

      {/* A failed remove used to change nothing on screen — the row stays
          (correctly: nothing was removed) but the user was told nothing. The
          server's sentence is shown exactly as returned: 409 while the
          environment is still building, 404 if already removed (safe to
          retry) — prefixed with the eval's own name so it's clear which row
          it belongs to. */}
      {backed && removeEval.isError && (
        <Alert severity="error" sx={{ mb: 2, typography: "s3" }}>
          {failedRemoveName ? `${failedRemoveName}: ` : ""}
          {removeEval.error?.detail || "Couldn’t remove the evaluation. Try again."}
        </Alert>
      )}

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
            {appliedEvals.map((e) => {
              const action = (
                // No always-on evals. Every added row is removable — except on a
                // locked template, where every edit is gated behind a fork.
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
                    <IconButton
                      size="small"
                      disabled={removeDisabled}
                      aria-label={EVALS_COPY.remove}
                      onClick={() => onRemove(e.id)}
                    >
                      <Iconify
                        icon="solar:trash-bin-trash-linear"
                        width={16}
                        sx={{ color: "text.subtitle" }}
                      />
                    </IconButton>
                  </Box>
                </Tooltip>
              );
              // A backed env's rows are §1 entries; a forked/template env's rows
              // are still the fixture catalogue shape the store holds.
              return backed ? (
                <SelectedEvalRow key={e.id} item={e} action={action} />
              ) : (
                <EvalRow key={e.id} item={e} action={action} />
              );
            })}
          </Stack>
        )}
      </SectionCard>

      {/* A backed env adds through the real §2/§3 picker (available list + the
          modality input mapping); a forked/template env keeps the store-only
          product picker. */}
      {backed ? (
        <AddEvaluationDrawer
          open={pickerOpen}
          env={env}
          onClose={() => setPickerOpen(false)}
        />
      ) : (
        <AddEvalsDrawer
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          env={env}
          envState={envState}
          existingIds={appliedIds}
          onAdd={add}
        />
      )}
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
