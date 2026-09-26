import { useMemo, useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Box, CircularProgress, Stack, Typography, Button, IconButton, Tooltip, Switch } from "@mui/material";
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
import { refusalText } from "./refusalText";
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
 * seed is guarded by a flag persisted in envState, so deliberately clearing
 * everything doesn't re-add them — even across a tab switch that remounts this
 * step; the user removes any of them from Added or opens the library for more.
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
  // Env-level "enable tool call evaluation". A backed env persists it through
  // PUT .../evaluations/tool-call/ and reads it back from the detail; a
  // forked/template env has no server counterpart, so it stays client state.
  const toolCall = useToolCallEval();

  // A real backend-backed env drives its applied set from the environment
  // detail (evaluations.selected) — the authoritative list the add and remove
  // endpoints mutate. A forked/template env has no backend counterpart, so it
  // stays store-driven (fixture-seeded preset). The detail query shares its
  // cache with useAddEvaluation's setQueryData, so an add reflects immediately.
  const detailQuery = useQuery(harnessEnvironmentQuery(env.id, { enabled: backed }));
  // Each `selected[]` row is a full catalogue entry plus `id` and
  // `runnable`. Kept
  // whole — the tab shows `source`, the cost line (`credits_per_run` /
  // `charges_judge_tokens`) and `inputs`, which the old {id,name,blurb}
  // projection threw away.
  const selectedFromDetail = useMemo(() => {
    const selected = detailQuery.data?.evaluations?.selected;
    return Array.isArray(selected) ? selected : [];
  }, [detailQuery.data]);

  // A failed or still-running detail read leaves `selectedFromDetail` empty,
  // which is indistinguishable from an environment that genuinely has no
  // evals — and the card below would then announce "Added (0)" and "No
  // evaluations added yet" about an environment that may hold eight. Neither
  // the count nor the empty state may be drawn until the list is actually
  // known, so both states are handled before the card.
  const detailUnknown = backed && (detailQuery.isPending || detailQuery.isError);

  const toolCallOn = backed
    ? !!detailQuery.data?.settings?.enable_tool_evaluation
    : !!envState?.toolCallEval;
  const setToolCall = (enabled) => {
    if (!backed) {
      patch({ toolCallEval: enabled });
      return;
    }
    toolCall.mutate({ envId: env.id, enabled });
  };

  const appliedEvals = backed ? selectedFromDetail : store.appliedEvals;
  const appliedIds = store.appliedIds;
  const { add } = store;

  // Remove. On a backed env, removal is a server soft-delete of
  // `evaluations.selected[].id`; the detail query is invalidated so the real set is
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

  // Auto-seed the preset into Added on first empty mount. The "already seeded"
  // flag is persisted in envState — NOT a component ref — so a deliberate
  // "remove all" survives a tab switch: a ref reset on remount and looped the
  // suggestions straight back in.
  const seeded = envState?.evalsSeeded === true;
  useEffect(() => {
    if (seeded) return;
    // A backed env's applied set is real (§6) — never seed fixtures into it,
    // and there's no store flag to keep (it reads §6, not envState).
    if (backed) return;
    // Wait for scenarios: don't burn the seed flag before the env is usable.
    if (needsScenarios) return;
    // No preset → there is nothing to seed and nothing that could loop back in,
    // so keep the flag (and any patches) out of it entirely.
    if ((env.evalPreset || []).length === 0) return;
    // Preset exists but the user already has evals: record that the seed
    // decision is made, so a later "remove all" survives a remount.
    if (appliedEvals.length > 0) { patch({ evalsSeeded: true }); return; }
    if (suggested.length === 0) { patch({ evalsSeeded: true }); return; }
    // Seed the preset and record it in one patch, so the flag can't be lost.
    patch({ evals: [...(envState?.evals || []), ...suggested], evalsSeeded: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seeded, backed, needsScenarios, suggested.length, appliedEvals.length]);

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

      {/* Tool-call evaluation. Gated on a connected agent — tool calls are read
          from it during the run. */}
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
              checked={toolCallOn && !!envState?.agent}
              disabled={locked || !envState?.agent || (backed && (detailUnknown || toolCall.isPending))}
              onChange={(e) => setToolCall(e.target.checked)}
              inputProps={{ "aria-label": EVALS_COPY.toolCall.title }}
            />
          </span>
        </Tooltip>
      </Stack>
      {backed && toolCall.isError && (
        <Alert severity="error" sx={{ mt: -2, mb: 3, typography: "s3" }}>
          {refusalText(toolCall.error, "Couldn’t change tool-call evaluation. Try again.")}
        </Alert>
      )}

      {/* A failed remove used to change nothing on screen — the row stays
          (correctly: nothing was removed) but the user was told nothing. The
          server's sentence is shown exactly as returned: 409 while the
          environment is still building, 404 if already removed (safe to
          retry) — prefixed with the eval's own name so it's clear which row
          it belongs to. */}
      {backed && removeEval.isError && (
        <Alert severity="error" sx={{ mb: 2, typography: "s3" }}>
          {failedRemoveName ? `${failedRemoveName}: ` : ""}
          {refusalText(removeEval.error, "Couldn’t remove the evaluation. Try again.")}
        </Alert>
      )}

      <SectionCard
        title={detailUnknown ? EVALS_COPY.addedTitleUnknown : EVALS_COPY.addedTitle(appliedEvals.length)}
        subtitle={!detailUnknown && appliedEvals.length ? EVALS_COPY.addedSubtitle : undefined}
      >
        {detailUnknown ? (
          detailQuery.isError ? (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn’t load evaluations"
              // The server's own sentence when it sent one; the fallback is
              // for a failure with no body at all.
              body={refusalText(detailQuery.error, EVALS_COPY.addedError)}
              action={
                <Button variant="outlined" size="small" onClick={() => detailQuery.refetch()}>
                  Retry
                </Button>
              }
            />
          ) : (
            <Stack alignItems="center" sx={{ py: 5 }}>
              <CircularProgress size={22} />
            </Stack>
          )
        ) : appliedEvals.length === 0 ? (
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
              // A backed env's rows are catalogue entries; a forked/template env's rows
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

      {/* A backed env adds through the real backed picker (available list + the
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
