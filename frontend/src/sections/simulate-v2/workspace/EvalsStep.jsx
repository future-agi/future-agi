import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";
import { getEval } from "../_mock/evals";
import { useAppliedEvals, EvalRow } from "./evals/appliedEvals";
import AddEvalsDrawer from "./evals/AddEvalsDrawer";

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
export default function EvalsStep({ env, envState, patch, onGo }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  // The workspace routes away from this step when there are no scenarios;
  // this is the backstop if it is ever rendered directly.
  const needsScenarios = envState.scenarios.length === 0;

  const { appliedEvals, appliedIds, add, remove } = useAppliedEvals(envState, patch);

  // Recommendations come from the environment, minus anything already applied.
  const recommended = useMemo(
    () => env.evalPreset.map(getEval).filter((e) => e && !appliedIds.has(e.id)),
    [env.evalPreset, appliedIds],
  );


  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 3 }}>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Evals</Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 720 }}>
            These decide whether each task passed. Pick them from the eval library and map
            their inputs onto what the run produces.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
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
                Add evals
              </Button>
            </span>
          </Tooltip>
          <Button
            variant="outlined"
            size="small"
            onClick={() => onGo("runs")}
            endIcon={<Iconify icon="solar:arrow-right-linear" width={15} />}
            sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
          >
            Continue
          </Button>
        </Stack>
      </Stack>

      {/* ── recommended for this environment ── */}
      {recommended.length > 0 && !needsScenarios && (
        <SectionCard
          title="Recommended for this environment"
          subtitle={`${recommended.length} evals we'd apply to ${env.name}`}
          sx={{ mb: 2 }}
          action={
            <Button
              size="small"
              onClick={() => add(recommended)}
              sx={{ typography: "s2", fontWeight: 700, color: "primary.main" }}
            >
              Add all {recommended.length}
            </Button>
          }
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {recommended.map((e) => (
              <EvalRow
                key={e.id}
                item={e}
                action={
                  <Button
                    size="small"
                    onClick={() => add([e])}
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
        title={`Applied evals (${appliedEvals.length})`}
        subtitle={appliedEvals.length ? "Every task is scored against these" : undefined}
      >
        {appliedEvals.length === 0 ? (
          <EmptyState
            icon={needsScenarios ? "solar:lock-keyhole-minimalistic-linear" : "solar:shield-check-linear"}
            title={needsScenarios ? "Add scenarios first" : "No evals yet"}
            body={
              needsScenarios
                ? "An eval scores the tasks a run produces, so it needs scenarios to point at. Add some and this unlocks."
                : "You can run without them — you'll get traces, but nothing will tell you whether the agent was right."
            }
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => (needsScenarios ? onGo("scenarios") : setPickerOpen(true))}
                endIcon={needsScenarios ? <Iconify icon="solar:arrow-right-linear" width={15} /> : null}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                {needsScenarios ? "Add scenarios" : "Add evals"}
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
                  e.required ? (
                    <Tooltip title="Always on for every run" arrow>
                      <Typography sx={{ typography: "s3", color: "text.subtitle", px: 1 }}>
                        Always on
                      </Typography>
                    </Tooltip>
                  ) : (
                    <IconButton size="small" onClick={() => remove(e.id)}>
                      <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
                    </IconButton>
                  )
                }
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
    </Box>
  );
}

EvalsStep.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
};
