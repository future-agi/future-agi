import { useMemo, useState } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import ScenarioToolbar from "./ScenarioToolbar";
import ScenarioTable from "./ScenarioTable";
import GroupedScenarioList from "./GroupedScenarioList";
import CoverageMatrix from "./CoverageMatrix";
import AddScenariosDrawer from "./AddScenariosDrawer";
import ScenarioEditor from "./ScenarioEditor";
import PropTypes from "prop-types";
import { SCENARIOS_COPY, deriveUseCase, groupScenarios } from "./scenarios.constants";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "./scenarios.shapes";

// The add CTA. Rendered in two places (header when the list is populated, and
// the empty placeholder), so it lives here as one node. Disabled with a
// coming-soon tooltip for now — the drawer it opens is built and wired, just
// not surfaced yet. Wrapped in a span so the tooltip still fires over the
// disabled button.
function AddButton({ onClick, contained = false }) {
  return (
    <CustomTooltip show arrow size="small" title={SCENARIOS_COPY.addComingSoon}>
      <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
        <Button
          variant={contained ? "contained" : "outlined"}
          color="primary"
          size="small"
          disabled
          onClick={onClick}
          aria-label={SCENARIOS_COPY.addLabel}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
        >
          {SCENARIOS_COPY.addLabel}
        </Button>
      </Box>
    </CustomTooltip>
  );
}
AddButton.propTypes = { onClick: PropTypes.func, contained: PropTypes.bool };

// Shown when the environment has no scenarios at all — rare, since they are
// normally derived when the environment is built.
function RoutePlaceholder({ onAdd }) {
  return (
    <EmptyState
      icon="solar:widget-add-linear"
      title={SCENARIOS_COPY.emptyTitle}
      body={SCENARIOS_COPY.emptyBody}
      action={<AddButton onClick={onAdd} contained />}
    />
  );
}
RoutePlaceholder.propTypes = { onAdd: PropTypes.func };

// Scenarios tab body. The scenarios are already here — derived when the
// environment is built — so the page leads with them, with the ways to add more
// beside the heading and per-row editing behind the pencil. Two views of the
// same rows share one toolbar so filters survive a view switch, and the coverage
// matrix below reads the live rows.
export default function ScenariosStep({ env, envState, patch }) {
  const [view, setView] = useState("table");
  const [query, setQuery] = useState("");
  const [selectedUseCases, setSelectedUseCases] = useState([]);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);

  const selected = envState?.scenarios || [];

  const allUseCases = useMemo(() => {
    const map = new Map();
    selected.forEach((r) => {
      const uc = deriveUseCase(r);
      if (!map.has(uc.id)) map.set(uc.id, uc);
    });
    return [...map.values()];
  }, [selected]);

  const q = query.trim().toLowerCase();
  const shown = selected.filter((r) => {
    if (selectedUseCases.length && !selectedUseCases.includes(deriveUseCase(r).id)) return false;
    if (!q) return true;
    const hay = `${r.name || ""} ${r.summary || ""} ${r.title || ""} ${r.task || ""} ${r.useCase || ""}`.toLowerCase();
    return hay.includes(q);
  });
  const shownGroups = groupScenarios(shown);

  const clearFilters = () => { setQuery(""); setSelectedUseCases([]); };
  const removeScenario = (id) => patch({ scenarios: selected.filter((s) => s.id !== id) });

  // Adds dedupe against what is already on the environment, so re-adding a row
  // the drawer still had selected is a no-op rather than a duplicate.
  const addScenarios = (rows) => {
    const existing = new Set(selected.map((s) => s.id));
    const fresh = (rows || []).filter((r) => !existing.has(r.id));
    if (fresh.length) patch({ scenarios: [...selected, ...fresh] });
  };

  // Edits replace the row in place, so a scenario keeps its id and everything
  // keyed off it — coverage, run history, the evals mapped to it.
  const saveScenario = (row) =>
    patch({ scenarios: selected.map((s) => (s.id === row.id ? row : s)) });

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "center" }}
        spacing={1.5}
        sx={{ mb: 2 }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
              {SCENARIOS_COPY.heading}
            </Typography>
            {selected.length > 0 && (
              <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                {`(${selected.length})`}
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {SCENARIOS_COPY.subtitle}
          </Typography>
        </Box>
        {selected.length > 0 && <AddButton onClick={() => setAdding(true)} contained />}
      </Stack>

      {selected.length === 0 ? (
        <RoutePlaceholder onAdd={() => setAdding(true)} />
      ) : (
        <>
          <SectionCard sx={{ mb: 2 }}>
            <ScenarioToolbar
              query={query}
              onQueryChange={setQuery}
              view={view}
              onViewChange={setView}
              allUseCases={allUseCases}
              countBy={(id) => selected.filter((r) => deriveUseCase(r).id === id).length}
              selectedUseCases={selectedUseCases}
              onUseCasesChange={setSelectedUseCases}
              shownCount={shown.length}
              totalCount={selected.length}
              onClear={clearFilters}
            />

            {shownGroups.length === 0 ? (
              <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  {SCENARIOS_COPY.noMatch}
                </Typography>
              </Box>
            ) : view === "table" ? (
              <ScenarioTable rows={shown} groups={shownGroups} env={env} onEdit={setEditing} onRemove={removeScenario} />
            ) : (
              <GroupedScenarioList groups={shownGroups} env={env} onEdit={setEditing} onRemove={removeScenario} />
            )}
          </SectionCard>

          <CoverageMatrix scenarios={selected} env={env} />
        </>
      )}

      <AddScenariosDrawer
        open={adding}
        onClose={() => setAdding(false)}
        env={env}
        selected={selected}
        onAdd={addScenarios}
      />
      <ScenarioEditor
        open={!!editing}
        onClose={() => setEditing(null)}
        row={editing}
        env={env}
        onSave={saveScenario}
      />
    </Box>
  );
}

ScenariosStep.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE.isRequired,
  patch: PropTypes.func.isRequired,
};
