import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, PersonaBadge, cardGrid } from "../components/primitives";
import { generatedPool } from "../_mock/scenarios";
import TemplatePicker from "./scenarios/TemplatePicker";
import ChatBuilder from "./scenarios/ChatBuilder";
import AgentDerived from "./scenarios/AgentDerived";
import DatasetImport from "./scenarios/DatasetImport";
import ScriptUpload from "./scenarios/ScriptUpload";
import {
  PackThumb, ChatThumb, DatasetThumb, ScriptThumb, AgentThumb, PickRouteIllustration,
} from "./scenarios/RouteThumbs";

const MODES = [
  {
    id: "templates",
    label: "Scenario pack",
    blurb: "Curated packs built for this environment — happy paths, edge cases and adversarial probes.",
    Thumb: PackThumb,
  },
  {
    id: "chat",
    label: "Describe in chat",
    blurb: "Say what you want to test in plain language and watch the scenarios build themselves.",
    Thumb: ChatThumb,
  },
  {
    id: "dataset",
    label: "From a dataset",
    blurb: "Choose a dataset and the columns that matter, and turn its rows into tasks.",
    Thumb: DatasetThumb,
  },
  {
    id: "script",
    label: "Upload a script",
    blurb: "Drop in a call script, SOP or runbook and we pull out the scenarios it describes.",
    Thumb: ScriptThumb,
  },
  {
    id: "agent",
    label: "From your agent",
    blurb: "We read your connected agent's prompt and tools, then write scenarios that probe its weak spots.",
    Thumb: AgentThumb,
  },
];

/**
 * Scenarios.
 *
 * Three routes to the same destination — a list of runnable tasks. Personas are
 * folded into the scenario rather than picked separately, so what lands in the
 * table below is already everything a run needs.
 */
export default function ScenariosStep({ env, envState, patch, onGo }) {
  // Nothing is chosen up front: the routes are the question this screen asks,
  // and pre-answering it hides the other four. The chosen route renders in
  // place below, so route, its UI and the scenarios it produces stay on one page.
  const [mode, setMode] = useState(null);
  const selected = envState.scenarios;

  const addScenarios = (rows, source) => {
    const existing = new Set(selected.map((s) => s.id));
    const merged = [...selected, ...rows.filter((r) => !existing.has(r.id))];
    patch({ scenarios: merged, scenarioSource: source });
  };

  const removeScenario = (id) =>
    patch({ scenarios: selected.filter((s) => s.id !== id) });

  return (
    <Box sx={{ p: 2 }}>
      {/*
        Heading above, routes below. The reference puts the heading beside the
        cards, but it only has three: five of these need the full width, and
        wrapping the fifth onto its own line is worse than moving the title.
        Each card is a thumbnail and a name; the sentence explaining it is a
        tooltip, because five paragraphs side by side is not scannable.
      */}
      <Box sx={{ mb: 2 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Scenarios</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Each scenario is one task your agent has to complete, and carries its own persona.
        </Typography>
      </Box>

      <Box sx={{ ...cardGrid(170), gap: 1.5, mb: 3 }}>
        {MODES.map((m) => (
          <ModeCard
            key={m.id}
            mode={m}
            selected={mode === m.id}
            onClick={() => setMode(mode === m.id ? null : m.id)}
            disabled={m.id === "agent" && !envState.agent}
          />
        ))}
      </Box>

      {/* Nothing picked yet — show what this step produces rather than a blank. */}
      <Box sx={{ mb: 2.5 }}>
        {!mode && <RoutePlaceholder env={env} />}
        {mode === "templates" && (
          <TemplatePicker env={env} onAdd={(r) => addScenarios(r, "templates")} selected={selected} />
        )}
        {mode === "chat" && <ChatBuilder env={env} onAdd={(r) => addScenarios(r, "chat")} />}
        {mode === "agent" && (
          <AgentDerived env={env} envState={envState} onAdd={(r) => addScenarios(r, "agent")} />
        )}
        {mode === "dataset" && (
          <DatasetImport env={env} onAdd={(r) => addScenarios(r, "dataset")} selected={selected} />
        )}
        {mode === "script" && (
          <ScriptUpload env={env} onAdd={(r) => addScenarios(r, "script")} selected={selected} />
        )}
      </Box>

      {/*
        Only once something has been added. An empty table under an empty
        route panel is two placeholders stacked saying the same thing, and the
        preview above already shows what a scenario looks like.
      */}
      {selected.length > 0 && (
        <SectionCard
          title={`Selected scenarios (${selected.length})`}
          subtitle="These will run against your agent"
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => onGo("evals")}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Add evals
            </Button>
          }
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {selected.map((s, i) => (
              <ScenarioRow key={s.id} row={s} index={i} onRemove={() => removeScenario(s.id)} />
            ))}
          </Stack>
        </SectionCard>
      )}
    </Box>
  );
}

ScenariosStep.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
};

function ModeCard({ mode, onClick, disabled, selected }) {
  const { Thumb } = mode;
  return (
    <Tooltip
      arrow
      placement="top"
      title={disabled ? "Connect an agent first" : mode.blurb}
    >
      <Box
        onClick={disabled ? undefined : onClick}
        sx={{
          borderRadius: 1.5, overflow: "hidden", height: "100%",
          border: "1px solid",
          borderColor: selected ? "primary.main" : "divider",
          bgcolor: "background.paper",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          transition: "border-color .16s ease",
          "&:hover": disabled || selected ? {} : { borderColor: "text.subtitle" },
        }}
      >
        <Box
          sx={{
            height: 100, display: "grid", placeItems: "center",
            bgcolor: (t) => selected
              ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.1 : 0.04)
              : "background.neutral",
            borderBottom: "1px solid", borderColor: "divider",
          }}
        >
          <Thumb />
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ px: 1.5, py: 1.125 }}>
          <Typography noWrap sx={{ flex: 1, typography: "s2", fontWeight: 600 }}>
            {mode.label}
          </Typography>
          {selected && (
            <Iconify icon="solar:check-circle-bold" width={14} sx={{ color: "primary.main", flexShrink: 0 }} />
          )}
        </Stack>
      </Box>
    </Tooltip>
  );
}
ModeCard.propTypes = {
  mode: PropTypes.object, onClick: PropTypes.func,
  disabled: PropTypes.bool, selected: PropTypes.bool,
};

/**
 * Shown until a route is picked.
 *
 * A blank half-screen makes the step look unfinished, so this shows what the
 * step is for: the shape of a scenario, built from this environment's own
 * derived rows rather than invented ones.
 */
function RoutePlaceholder({ env }) {
  const sample = generatedPool(env).slice(0, 3);

  return (
    <Box sx={{ py: 5, px: 2, textAlign: "center" }}>
      <PickRouteIllustration />
      <Typography sx={{ typography: "m2", fontWeight: 600, mt: 2 }}>
        Pick a route above to start
      </Typography>
      <Typography sx={{ typography: "s1", color: "text.subtitle", mt: 0.5 }}>
        Whichever you choose, you end up with a list of tasks like this.
      </Typography>

      <Box
        sx={{
          maxWidth: 640, mx: "auto", mt: 3, textAlign: "left",
          border: "1px solid", borderColor: "divider", borderRadius: 1.5,
          overflow: "hidden", bgcolor: "background.paper",
        }}
      >
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {sample.map((r) => (
            <Stack key={r.id} direction="row" alignItems="center" spacing={2} sx={{ px: 2, py: 1.25 }}>
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{r.title}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{r.task}</Typography>
              </Box>
              <Box sx={{ width: 180, flexShrink: 0, display: { xs: "none", md: "block" } }}>
                <PersonaBadge persona={r.persona} compact />
              </Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                ~{r.turns}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>

      <Typography sx={{ typography: "s3", color: "text.subtitle", fontStyle: "italic", mt: 1.5 }}>
        Sample preview — pick a route above to build your own.
      </Typography>
    </Box>
  );
}
RoutePlaceholder.propTypes = { env: PropTypes.object.isRequired };

export function ScenarioRow({ row, index, onRemove, selectable, checked, onToggle }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        px: 2.5, py: 1.5,
        cursor: selectable ? "pointer" : "default",
        "&:hover": selectable ? { bgcolor: "action.hover" } : {},
      }}
      onClick={selectable ? onToggle : undefined}
    >
      {selectable ? (
        <Iconify
          icon={checked ? "solar:check-square-bold" : "solar:stop-linear"}
          width={18}
          sx={{ color: checked ? "primary.main" : "text.subtitle", flexShrink: 0 }}
        />
      ) : (
        <Typography sx={{ typography: "s3", color: "text.subtitle", width: 20, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
          {index + 1}
        </Typography>
      )}

      <Box sx={{ flex: 1.4, minWidth: 0 }}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{row.title}</Typography>
          {row.critical && (
            <Tooltip title="Critical — a failure here is a release blocker" arrow>
              <Box sx={{ display: "flex" }}>
                <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626" }} />
              </Box>
            </Tooltip>
          )}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{row.task}</Typography>
      </Box>

      {/* Wide enough for a full job title — roles were truncating at 180. */}
      <Box sx={{ width: 240, flexShrink: 0, display: { xs: "none", md: "block" } }}>
        <PersonaBadge persona={row.persona} compact />
      </Box>

      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ width: 62, flexShrink: 0, display: { xs: "none", sm: "flex" } }}>
        <Iconify icon="solar:chat-round-line-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>~{row.turns}</Typography>
      </Stack>

      {onRemove && (
        <IconButton size="small" onClick={(e) => { e.stopPropagation(); onRemove(); }}>
          <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
        </IconButton>
      )}
    </Stack>
  );
}
ScenarioRow.propTypes = {
  row: PropTypes.object, index: PropTypes.number, onRemove: PropTypes.func,
  selectable: PropTypes.bool, checked: PropTypes.bool, onToggle: PropTypes.func,
};
