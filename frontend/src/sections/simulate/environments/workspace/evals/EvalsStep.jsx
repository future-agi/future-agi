import { useMemo, useState } from "react";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { getEval } from "src/api/simulate-environments/_fixtures/evalCatalog";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import EvalRow from "./EvalRow";
import AddEvalsDrawer from "./AddEvalsDrawer";
import { useAppliedEvals } from "./useAppliedEvals";
import { EVALS_COPY, ENV_SHAPE, ENV_STATE_SHAPE } from "./evals.constants";
import PropTypes from "prop-types";

/**
 * Evaluations tab.
 *
 * A Suggested/Added split: Suggested is the environment's preset minus anything
 * already applied, so adding a suggestion moves it into Added and removing it
 * from Added returns it here — the Suggested list is derived, not stored.
 * "Add evaluations" opens the product's eval picker for the rest of the library.
 *
 * The designer's twin-backed suggestions and clone-eval editor are out of
 * scope for this phase and are not ported.
 */
export default function EvalsStep({ env, envState, patch, onGo }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  // The workspace routes away from this tab when there are no scenarios; this
  // is the backstop if it is ever rendered directly without them.
  const needsScenarios = (envState?.scenarios?.length || 0) === 0;

  const { appliedEvals, appliedIds, add, remove } = useAppliedEvals(envState, patch);

  // Suggested = the environment's preset MINUS anything already added.
  const suggested = useMemo(() => {
    const presetIds = env.evalPreset || [];
    return presetIds
      .map(getEval)
      .filter(Boolean)
      .filter((e) => !appliedIds.has(e.id))
      .filter((e, i, arr) => arr.findIndex((x) => x.id === e.id) === i);
  }, [env.evalPreset, appliedIds]);

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
          <Tooltip arrow title={needsScenarios ? EVALS_COPY.needsScenariosHint : ""}>
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled={needsScenarios}
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

      {suggested.length > 0 && !needsScenarios && (
        <SectionCard
          title={EVALS_COPY.suggestedTitle(suggested.length)}
          subtitle={EVALS_COPY.suggestedSubtitle}
          sx={{ mb: 2 }}
          action={
            <Button
              size="small"
              variant="outlined"
              onClick={() => add(suggested)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
              sx={{
                typography: "s2",
                fontWeight: "fontWeightBold",
                textTransform: "none",
                color: "primary.main",
                borderColor: (t) => alpha(t.palette.primary.main, 0.4),
                "&:hover": {
                  borderColor: "primary.main",
                  bgcolor: (t) =>
                    alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.08 : 0.04),
                },
              }}
            >
              {EVALS_COPY.addAll(suggested.length)}
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
                    sx={{
                      typography: "s2",
                      fontWeight: "fontWeightBold",
                      color: "primary.main",
                      minWidth: 0,
                    }}
                  >
                    {EVALS_COPY.addOne}
                  </Button>
                }
              />
            ))}
          </Stack>
        </SectionCard>
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
            body={
              needsScenarios
                ? EVALS_COPY.lockedBody
                : suggested.length > 0
                  ? EVALS_COPY.emptyWithSuggestions
                  : EVALS_COPY.emptyNoSuggestions
            }
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => (needsScenarios ? onGo?.("scenarios") : setPickerOpen(true))}
                endIcon={
                  needsScenarios ? <Iconify icon="solar:arrow-right-linear" width={15} /> : null
                }
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                {needsScenarios ? EVALS_COPY.addScenarios : EVALS_COPY.add}
              </Button>
            }
          />
        ) : (
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {appliedEvals.map((e) => (
              <EvalRow
                key={e.id}
                item={e}
                action={
                  // No always-on evals. Every added row is removable.
                  <IconButton size="small" aria-label={EVALS_COPY.remove} onClick={() => remove(e.id)}>
                    <Iconify
                      icon="solar:close-circle-linear"
                      width={16}
                      sx={{ color: "text.subtitle" }}
                    />
                  </IconButton>
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
};
