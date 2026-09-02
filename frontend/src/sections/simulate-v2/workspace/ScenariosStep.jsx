import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useSnackbar } from "notistack";
import {
  Box, Stack, Typography, Button, Tooltip, IconButton, Tab,
  TextField, Popover, Checkbox, InputBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { alpha } from "@mui/material/styles";
import { SectionCard, PersonaBadge } from "../components/primitives";
import { generatedPool } from "../_mock/scenarios";
import { staleScenarios, proofStatus, reproved, markEdited, INVALIDATING } from "../_mock/proofs";
import ScenarioDetail from "../components/ScenarioDetail";
import CoverageMatrix from "./scenarios/CoverageMatrix";
import AddScenariosDrawer from "./scenarios/AddScenariosDrawer";
import ScenarioEditor from "./scenarios/ScenarioEditor";
import ScenarioTable from "./scenarios/ScenarioTable";
import GateRejects from "./scenarios/GateRejects";
import { PickRouteIllustration } from "./scenarios/RouteThumbs";

/**
 * Scenarios.
 *
 * The scenarios are already here: they are derived from the agent when the
 * environment is built. So the page leads with them, and the ways to add more
 * live behind a button — five route cards across the top said the opposite,
 * that nothing had happened yet and a route had to be chosen first.
 *
 * Two views of the same rows. The list expands one scenario into its brief,
 * its checks and the proof it is passable. The table puts thirty of them side
 * by side on the derived axes, which is the view you want when the question is
 * what is in here rather than what is this.
 */
export default function ScenariosStep({ env, envState, patch, buildMode }) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  /* Default to the table view — it's the denser, more scannable
     shape and it's what the product team wanted users to land on. */
  const [view, setView] = useState("table");
  /*
    Search + use-case filter are shared by both views now. They used to
    live inside the list-only toolbar, which meant switching to the
    table lost your filters. Lifting to the parent lets the toolbar sit
    between the page heading and either body, and both views read the
    same `shown` rows.
  */
  const [query, setQuery] = useState("");
  const [selectedUseCases, setSelectedUseCases] = useState([]);
  const [filterAnchor, setFilterAnchor] = useState(null);
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
  const anyFilter = q.length > 0 || selectedUseCases.length > 0;

  /*
    Adding scenarios in real life isn't instant — every row goes
    through pre-verification (proved solvable, non-vacuous, pointed
    at a rule) before it lands on the environment. We stand in for
    that here with a scripted ~800ms delay so the "Adding…" toast
    reads as a real status, then swap it for the success toast
    once the patch actually fires. If none of the rows were new
    (all duplicates against what's already selected) we say so
    instead of claiming a nonexistent add.
  */
  const addScenarios = (rows, source) => {
    const existing = new Set(selected.map((s) => s.id));
    const fresh = (rows || []).filter((r) => !existing.has(r.id));
    const attempted = rows?.length || 0;
    const dupeCount = attempted - fresh.length;

    /* Toast up front, so the user knows the click landed even though
       the drawer is already sliding closed. */
    const pendingKey = enqueueSnackbar(
      attempted === 1 ? "Adding 1 scenario…" : `Adding ${attempted} scenarios…`,
      { variant: "info", persist: true },
    );

    setTimeout(() => {
      closeSnackbar(pendingKey);
      if (fresh.length === 0) {
        enqueueSnackbar(
          attempted === 1 ? "That scenario was already added." : "Those scenarios were already added.",
          { variant: "info" },
        );
        return;
      }
      patch({ scenarios: [...selected, ...fresh], scenarioSource: source });
      const base = fresh.length === 1
        ? "1 scenario added"
        : `${fresh.length} scenarios added`;
      const suffix = dupeCount > 0
        ? ` · ${dupeCount} already on this environment`
        : "";
      enqueueSnackbar(`${base}${suffix}`, { variant: "success" });
    }, 800);
  };

  const removeScenario = (id) =>
    patch({ scenarios: selected.filter((s) => s.id !== id) });

  /* Edits replace the row in place, so a scenario keeps its id and everything
     keyed off it — coverage, run history, the evals mapped to it.

     And an edited scenario is an unproved one: the proof was of the task as it
     read before. Stamping the edit is what makes it show up in the banner
     above rather than keeping a green tick it no longer earns. */
  const saveScenario = (row) =>
    patch({ scenarios: selected.map((s) => (s.id === row.id ? markEdited(row) : s)) });

  /* The chosen depth overrides the environment's own, and every generation
     route is handed the adjusted environment rather than the original. */
  const depth = envState.difficulty || env.difficulty || "Advanced";
  const genEnv = { ...env, difficulty: depth };

  /*
    Scenarios are proved against a version of the world, and this environment
    has moved since some of them were proved. Nothing about that breaks loudly
    — the runs still produce numbers — so it has to be said here.

    In buildMode this is suppressed: the environment was just derived, it is
    on v1 by definition and nothing has drifted. Showing "12 of 32 need
    re-proving · this environment is on v3" on a freshly-built environment
    was reading a workspace demo story into a screen where none of it had
    happened yet.
  */
  const stale = buildMode ? [] : staleScenarios(selected, env, envState);
  const staleReasons = [...new Set(stale.flatMap((s) => proofStatus(s, env, envState).reasons))];
  /* Edited rows are a different story from world drift, and the banner says
     which of the two it is looking at. */
  const edited = stale.filter((s) => proofStatus(s, env, envState).edited);

  return (
    <Box sx={{ p: 2 }}>
      {/*
        Title and the add button on one line. Adding is a secondary action on
        this screen — the scenarios are already derived — so it is an outlined
        button beside the heading rather than five cards above it.
      */}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "center" }}
        spacing={1.5}
        sx={{ mb: 2 }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Scenarios</Typography>
            {selected.length > 0 && (
              <Typography sx={{ typography: "s1", fontWeight: 500, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                ({selected.length})
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Each scenario is one task your agent has to complete, and carries its own persona.
          </Typography>
        </Box>
        {/*
          Only show the header CTA once there are scenarios in the
          list. When it's empty, the RoutePlaceholder below already
          renders a prominent Add scenarios button — two CTAs for the
          same action read as noise (same pattern the Evaluations tab
          uses).
        */}
        {selected.length > 0 && (
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() => setAdding(true)}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
          >
            Add scenarios
          </Button>
        )}
      </Stack>

      {stale.length > 0 && (
        <Stack
          direction="row" alignItems="flex-start" spacing={1.5}
          sx={{
            mb: 2, px: 2.5, py: 1.75, borderRadius: 1.5, border: "1px solid",
            borderColor: alpha("#CA8A04", 0.35),
            bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Iconify icon="solar:danger-triangle-bold" width={16} sx={{ color: "#CA8A04", flexShrink: 0, mt: "2px" }} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>
              {stale.length} of {selected.length} scenarios need re-proving
              {edited.length > 0 && stale.length > edited.length
                ? ` — ${edited.length} edited, ${stale.length - edited.length} outgrown by the world`
                : edited.length === stale.length ? " after being edited" : ""}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              This environment is on {proofStatus(stale[0], env, envState).current}.{" "}
              {staleReasons.map((r) => INVALIDATING[r]).join("; ")}. They will still run and still
              report a number — the number is just no longer standing on a proof.
            </Typography>
          </Box>
          <Button
            variant="contained" color="primary" size="small"
            onClick={() => patch({ scenarios: reproved(selected, env, envState) })}
            startIcon={<Iconify icon="solar:refresh-circle-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
          >
            Re-prove {stale.length}
          </Button>
        </Stack>
      )}

      {selected.length === 0 ? (
        <RoutePlaceholder env={genEnv} onAdd={() => setAdding(true)} />
      ) : (
        /*
          No title/subtitle on the section card — the page heading above
          already names this list. The card's top row is a shared
          toolbar (search · filter · list/table tabs) that both views
          read from, so filters survive a view switch.
        */
        <SectionCard sx={{ mb: 2 }}>
          <Stack
            direction="row" alignItems="center" spacing={1}
            sx={{ px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <TextField
              size="small"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search scenarios by name, task or use case…"
              InputProps={{
                sx: { typography: "s2" },
                startAdornment: (
                  <Box sx={{ pr: 0.75, pl: 0.25, display: "flex", color: "text.subtitle" }}>
                    <Iconify icon="solar:magnifer-linear" width={14} />
                  </Box>
                ),
              }}
              sx={{ maxWidth: 380, flex: 1 }}
            />
            <Button
              size="small" variant="outlined"
              onClick={(e) => setFilterAnchor(e.currentTarget)}
              startIcon={<Iconify icon="mage:filter" width={14} />}
              endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
              sx={{
                typography: "s2", fontWeight: 700, textTransform: "none",
                color: selectedUseCases.length ? "primary.main" : "text.primary",
                borderColor: selectedUseCases.length ? "primary.main" : "divider",
              }}
            >
              Filter{selectedUseCases.length ? ` (${selectedUseCases.length})` : ""}
            </Button>
            {anyFilter && (
              <>
                <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
                  {shown.length} of {selected.length}
                </Typography>
                <Button
                  size="small"
                  onClick={() => { setQuery(""); setSelectedUseCases([]); }}
                  sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
                >
                  Clear
                </Button>
              </>
            )}
            <Box sx={{ flex: 1 }} />
            <SegmentedTabs value={view} onChange={(_, v) => setView(v)} sx={{ flexShrink: 0 }}>
              <Tab value="table" label="Table" />
              <Tab value="list" label="List" />
            </SegmentedTabs>
          </Stack>

          <UseCaseFilterPopover
            anchorEl={filterAnchor}
            onClose={() => setFilterAnchor(null)}
            allUseCases={allUseCases}
            countBy={(id) => selected.filter((r) => deriveUseCase(r).id === id).length}
            selected={selectedUseCases}
            onChange={setSelectedUseCases}
          />

          {shownGroups.length === 0 ? (
            <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                No scenarios match your filters.
              </Typography>
            </Box>
          ) : view === "table" ? (
            <ScenarioTable
              rows={shown}
              groups={shownGroups}
              env={env}
              onEdit={setEditing}
              onRemove={removeScenario}
            />
          ) : (
            <GroupedScenarioList
              groups={shownGroups}
              env={env}
              envState={envState}
              buildMode={buildMode}
              onEdit={setEditing}
              onRemove={removeScenario}
            />
          )}
        </SectionCard>
      )}

      {/*
        What the gates discarded, directly under the list they filtered — the
        rows above are the survivors, and the count is what makes that legible.
      */}
      {selected.length > 0 && <GateRejects env={env} kept={selected.length} sx={{ mb: 2 }} />}

      {/*
        Coverage after the list. It reads the rows above rather than
        introducing them — what is missing is only a question once you have
        seen what is there.
      */}
      {selected.length > 0 && <CoverageMatrix scenarios={selected} env={env} />}

      <AddScenariosDrawer
        open={adding}
        onClose={() => setAdding(false)}
        env={genEnv}
        envState={envState}
        selected={selected}
        onAdd={addScenarios}
      />
      <ScenarioEditor
        open={!!editing}
        onClose={() => setEditing(null)}
        row={editing}
        env={env}
        envState={envState}
        onSave={saveScenario}
      />
    </Box>
  );
}

ScenariosStep.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  buildMode: PropTypes.bool,
};


/**
 * Shown when the environment has no scenarios at all.
 *
 * Rare — they are normally derived from the agent — but a blank half-screen
 * makes the step look broken rather than empty, so this shows what the step
 * produces: the shape of a scenario, built from this environment's own derived
 * rows rather than invented ones.
 */
function RoutePlaceholder({ env, onAdd }) {
  const sample = generatedPool(env).slice(0, 3);

  return (
    <Box sx={{ py: 5, px: 2, textAlign: "center" }}>
      <PickRouteIllustration />
      <Typography sx={{ typography: "m2", fontWeight: 600, mt: 2 }}>
        No scenarios yet
      </Typography>
      <Typography sx={{ typography: "s1", color: "text.subtitle", mt: 0.5 }}>
        Whichever route you add from, you end up with a list of tasks like this.
      </Typography>
      <Button
        variant="contained"
        color="primary"
        size="small"
        onClick={onAdd}
        startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
        sx={{ typography: "s2", fontWeight: 700, mt: 2 }}
      >
        Add scenarios
      </Button>

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
        Sample preview — add scenarios to build your own.
      </Typography>
    </Box>
  );
}
RoutePlaceholder.propTypes = { env: PropTypes.object.isRequired, onAdd: PropTypes.func };

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

/* ── grouping ────────────────────────────────────────────────────────────── */

/**
 * Use-case-based grouping.
 *
 * Kind buckets (Tool use / Rule enforcement / Data traps / …) group by
 * how the scenario was *derived*, which is a builder concept. A reader
 * scanning 30+ scenarios cares about *what task the agent is being
 * asked to do*: verify identity, look up an order, refuse a refund,
 * handle a saved-card record. Those are the real use cases and they
 * cut across the derivation kinds.
 *
 * The scenario's title carries this cleanly:
 *   Core   → "Routine task using {tool_name}"  → group by the tool
 *   Rule   → the rule text (short, one per rule)
 *   Trap   → "{table_name}: {note}"            → group by the table
 *   Adv    → the template title (Instruction override, Authority claim…)
 *   Edge   → the template title
 *
 * So every group's label is what the scenarios in it are actually
 * doing, not the builder bucket they fell out of.
 */
const humanize = (s = "") => s
  .replace(/[_-]/g, " ")
  .replace(/\b\w/g, (c) => c.toUpperCase())
  .trim();

const deriveUseCase = (row) => {
  /*
    The scenario now carries its own use-case sentence — the mock stamps
    `row.useCase` with a spec-line description of what the group tests
    ("Verify a caller's identity before touching account state"). Group
    key is a slug of that sentence so scenarios with matching text land
    in the same group. Legacy scenarios that predate the field fall back
    to the previous id-based derivation.
  */
  if (row?.useCase) {
    const slug = row.useCase.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return { id: slug, label: row.useCase };
  }
  const id = row?.id || "";
  const title = row?.title || "";
  if (id.includes("-core-")) {
    const m = title.match(/(?:using|needing|landing on)\s+([\w_-]+)/i);
    if (m) return { id: `tool:${m[1].toLowerCase()}`, label: humanize(m[1]) };
    return { id: "tool:other", label: "Tool use" };
  }
  if (id.includes("-rule-")) {
    const short = title.split(/[.:]/)[0].slice(0, 42).trim();
    return { id: `rule:${short.toLowerCase()}`, label: short || "Rule enforcement" };
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table) return { id: `trap:${table.toLowerCase()}`, label: `${humanize(table)} data` };
    return { id: "trap:other", label: "Data traps" };
  }
  if (id.includes("-adversarial-")) return { id: `adv:${title.toLowerCase()}`, label: title || "Adversarial" };
  if (id.includes("-edge-")) return { id: `edge:${title.toLowerCase()}`, label: title || "Edge case" };
  return { id: "other", label: "Other" };
};

const groupScenarios = (rows) => {
  const buckets = new Map();
  rows.forEach((r) => {
    const uc = deriveUseCase(r);
    if (!buckets.has(uc.id)) buckets.set(uc.id, { id: uc.id, label: uc.label, rows: [] });
    buckets.get(uc.id).rows.push(r);
  });
  /* Bigger groups first — the tail of one-off adversarial/edge titles
     shouldn't push the meaty use-case groups out of view. */
  return [...buckets.values()].sort((a, b) => b.rows.length - a.rows.length);
};

/**
 * Just the grouped body — search, filter and view tabs live at the
 * page level now so the same toolbar drives both list and table
 * views. This renders each use-case section as a collapsible block
 * with a sticky header.
 */
function GroupedScenarioList({ groups, env, envState, buildMode, onEdit, onRemove }) {
  return (
    <Box>
      {groups.map((g) => (
        <CollapsibleGroup
          key={g.id}
          group={g}
          env={env}
          envState={envState}
          buildMode={buildMode}
          onEdit={onEdit}
          onRemove={onRemove}
        />
      ))}
    </Box>
  );
}
GroupedScenarioList.propTypes = {
  groups: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
  buildMode: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
};

/**
 * One collapsible group section. Header stays sticky when expanded so
 * scanning a big group keeps the current use case pinned at the top.
 * Chevron flips right → down on toggle. Header row is the whole click
 * target so there's no tiny hit area.
 */
function CollapsibleGroup({ group, env, envState, buildMode, onEdit, onRemove }) {
  const [open, setOpen] = useState(true);

  return (
    <Box>
      {/*
        Group header is a distinctive strip — neutral background, larger
        label typography, count as a pill — so the eye can tell at a
        glance "this is a use case group" versus "this is a scenario
        inside it". Before this both used the same s2/700/primary and
        the two levels blended into one long list.
      */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        onClick={() => setOpen((o) => !o)}
        sx={{
          position: "sticky", top: 0, zIndex: 2, cursor: "pointer",
          px: 2.5, py: 1.75,
          bgcolor: "background.neutral",
          borderBottom: "1px solid", borderColor: "divider",
          borderTop: "1px solid", borderTopColor: "divider",
          "&:hover": {
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
          },
        }}
      >
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={15}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography
          sx={{
            typography: "s1", fontWeight: 700, color: "text.primary",
            flex: 1, minWidth: 0,
          }}
        >
          {group.label}
        </Typography>
        <Typography
          sx={{
            px: 1, py: 0.25, borderRadius: 0.75,
            typography: "s3", fontWeight: 700, color: "text.secondary",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.06),
            fontVariantNumeric: "tabular-nums", flexShrink: 0,
            letterSpacing: 0.2,
          }}
        >
          {group.rows.length} {group.rows.length === 1 ? "scenario" : "scenarios"}
        </Typography>
      </Stack>

      {open && (
        /*
          Scenario rows are indented and share a subtle left rail so
          they visibly nest inside the group above. The rail is the
          single strongest signal that "these all belong together
          under the header you just read".
        */
        <Box sx={{ pl: 3 }}>
          <Stack
            divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
            sx={{
              borderLeft: "2px solid",
              borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
            }}
          >
            {group.rows.map((s) => (
              <Stack key={s.id} direction="row" alignItems="flex-start">
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <ScenarioDetail row={s} env={env} envState={envState} buildMode={buildMode} />
                </Box>
                <Tooltip arrow title="Edit scenario">
                  <IconButton size="small" onClick={() => onEdit(s)} sx={{ mt: 1, flexShrink: 0 }}>
                    <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Tooltip>
                <Tooltip arrow title="Remove from this environment">
                  <IconButton size="small" onClick={() => onRemove(s.id)} sx={{ mt: 1, mr: 1.5, flexShrink: 0 }}>
                    <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Tooltip>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
CollapsibleGroup.propTypes = {
  group: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  buildMode: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
};

/* ── filter popover ──────────────────────────────────────────────────────── */

/**
 * Use-case filter popover.
 *
 * The old MUI Menu clipped long use-case sentences off the right edge
 * and read as one dense list. This is a small custom popover instead:
 * fixed width, wrapping labels, an inline search for long lists, and
 * a clear header + footer treatment so the frame reads as a real
 * filter panel rather than a menu.
 */
function UseCaseFilterPopover({ anchorEl, onClose, allUseCases, countBy, selected, onChange }) {
  const [q, setQ] = useState("");

  /* Reset the internal search when the popover closes so it opens
     fresh next time. */
  const handleClose = () => { setQ(""); onClose(); };

  const filtered = q.trim()
    ? allUseCases.filter((uc) => uc.label.toLowerCase().includes(q.trim().toLowerCase()))
    : allUseCases;

  const toggle = (id) => onChange(selected.includes(id)
    ? selected.filter((v) => v !== id)
    : [...selected, id]);

  return (
    <Popover
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: 420,
            mt: 0.75,
            borderRadius: 1.5,
            border: "1px solid",
            borderColor: "divider",
            boxShadow: (t) => t.customShadows?.dropdown || t.shadows[6],
            overflow: "hidden",
          },
        },
      }}
    >
      {/* header */}
      <Stack
        direction="row" alignItems="center"
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="mage:filter" width={14} sx={{ color: "text.secondary", mr: 0.75 }} />
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>
          Filter by use case
        </Typography>
        {selected.length > 0 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mr: 0.75 }}>
            {selected.length} selected
          </Typography>
        )}
      </Stack>

      {/* inline search — proper bordered input so it reads as a real
          field. The old borderless neutral pill looked like a
          placeholder that never rendered. Height and padding match
          the other TextFields in this flow. */}
      {allUseCases.length > 6 && (
        <Box sx={{ px: 1.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box
            sx={{
              display: "flex", alignItems: "center", gap: 0.75,
              px: 1.25, height: 34, borderRadius: 1,
              border: "1px solid", borderColor: "divider",
              bgcolor: "background.paper",
              transition: "border-color .12s ease",
              "&:focus-within": {
                borderColor: (t) => t.palette.mode === "dark"
                  ? alpha(t.palette.text.primary, 0.35)
                  : "#7857FC",
              },
            }}
          >
            <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            <InputBase
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search use cases…"
              autoFocus
              sx={{ typography: "s2", flex: 1, color: "text.primary" }}
            />
            {q && (
              <IconButton size="small" onClick={() => setQ("")} sx={{ p: 0.25 }}>
                <Iconify icon="solar:close-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
              </IconButton>
            )}
          </Box>
        </Box>
      )}

      {/* list */}
      <Box sx={{ maxHeight: 340, overflowY: "auto", py: 0.5 }}>
        {filtered.length === 0 ? (
          <Box sx={{ px: 2, py: 4, textAlign: "center" }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              No use cases match &ldquo;{q}&rdquo;.
            </Typography>
          </Box>
        ) : (
          filtered.map((uc) => {
            const on = selected.includes(uc.id);
            const count = countBy(uc.id);
            return (
              <Stack
                key={uc.id}
                direction="row" alignItems="flex-start" spacing={1.25}
                onClick={() => toggle(uc.id)}
                sx={{
                  px: 2, py: 1, cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Checkbox
                  size="small" checked={on} disableRipple
                  sx={{
                    p: 0, mt: "1px", flexShrink: 0,
                    color: "text.disabled",
                    "&.Mui-checked": { color: "#7857FC" },
                  }}
                />
                <Typography
                  sx={{
                    typography: "s2", color: "text.primary",
                    flex: 1, minWidth: 0,
                    /* wrap long use-case sentences instead of clipping
                       them off the right edge */
                    whiteSpace: "normal", lineHeight: 1.4,
                  }}
                >
                  {uc.label}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3", color: "text.subtitle",
                    flexShrink: 0, mt: "1px",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {count}
                </Typography>
              </Stack>
            );
          })
        )}
      </Box>

      {/* footer */}
      <Stack
        direction="row" alignItems="center" justifyContent="space-between"
        sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button
          size="small"
          onClick={() => onChange([])}
          disabled={selected.length === 0}
          sx={{
            typography: "s2", fontWeight: 600, textTransform: "none",
            color: selected.length ? "text.secondary" : "text.disabled",
            minWidth: 0, px: 0,
            "&:hover": { bgcolor: "transparent", color: "text.primary" },
          }}
        >
          Clear all
        </Button>
        <Button
          size="small" variant="contained"
          onClick={handleClose}
          sx={{
            typography: "s2", fontWeight: 700, textTransform: "none",
            /* White in both themes — matches the neutral chrome of the
               popover and reads as the primary action without pulling
               in the purple brand tone. */
            bgcolor: "common.white", color: "grey.900",
            boxShadow: "none",
            border: "1px solid",
            borderColor: (t) => alpha(t.palette.common.black, 0.08),
            "&:hover": {
              bgcolor: "common.white",
              boxShadow: "none",
              borderColor: (t) => alpha(t.palette.common.black, 0.2),
            },
          }}
        >
          Done
        </Button>
      </Stack>
    </Popover>
  );
}
UseCaseFilterPopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  allUseCases: PropTypes.array,
  countBy: PropTypes.func,
  selected: PropTypes.array,
  onChange: PropTypes.func,
};
