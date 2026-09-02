import PropTypes from "prop-types";
import { useMemo, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Tab, InputAdornment, TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs, SegmentedTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { ENVIRONMENT_TEMPLATES, groupByAgentGroup } from "../_mock/environments";
import { AGENT_TYPE_GROUPS, getAgentType } from "../_mock/agentTypes";
import { getSurface } from "../_mock/surfaces";
import { useSimStore } from "../store";
import MyEnvironmentsTable from "./MyEnvironmentsTable";
import TemplateSetupPanel from "./TemplateSetupPanel";
import { TwinComposer } from "./NewTwinEnvironment";
import { EnvironmentCardSkeleton } from "../components/loading";
import { EmptyState, cardGrid } from "../components/primitives";

/*
  Twins tab embeds the compose flow inline — multi-service picker,
  seed prompt, lifetime, name, provision. There's no separate catalog
  or right-hand setup panel because every twin selection is really a
  composition of one or more twins; treating N=1 and N>1 differently
  fragments the flow. The same picker handles the "I just want one"
  case as a natural subset of the composer.
*/

/** Filter controls sit above the content, so they stay out of its way. */
const compactInputSx = {
  "& .MuiInputBase-root": { typography: "s2", height: 34 },
  "& .MuiOutlinedInput-input": { py: 0 },
};

/**
 * The front door of the simulation flow.
 *
 * Templates is a pick-and-set-up surface: the catalogue on the left, and how to
 * stand the selected one up on the right. Setting one up moves it to My
 * environments, which is where the workspace lives — so browsing and working
 * are two different places rather than one list that changes meaning.
 */
export default function EnvironmentGallery() {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const [tab, setTab] = useState("templates");
  /*
    Sub-toggle inside the Templates tab — "Regular" is the classic
    agent-type-grouped gallery, "Twins" surfaces the twin-service
    catalog with brand logos and the twin-category groupings the
    user is used to seeing on the twins browse page.
  */
  const [kind, setKind] = useState("regular");
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("all");
  /*
    Backing filter — quick chip on My environments to filter by how
    the env's world is materialised. "twin" surfaces envs whose world
    is a live SaaS sandbox (twinBacking set); "generated" surfaces the
    seed-derived ones. Empty means show everything. Templates tab
    doesn't use this — templates don't have a twin backing yet.
  */
  const [backing, setBacking] = useState("all");
  const [selectedId, setSelectedId] = useState(ENVIRONMENT_TEMPLATES[0]?.id);
  const [loading, setLoading] = useState(true);

  // A short shaped load so the skeleton is actually seen — this is the state a
  // real fetch would sit in, and stakeholders should see it.
  useEffect(() => {
    const t = setTimeout(() => setLoading(false), 620);
    return () => clearTimeout(t);
  }, []);

  const mine = state.myEnvironments;
  /*
    Templates split into regular vs twin: `agentType === "twin_backed"`
    is the discriminator. The Templates tab then picks one via the
    `kind` toggle; My environments keeps the full list (twin envs
    still show up there, filtered via the backing chip).
  */
  const regularTemplates = useMemo(
    () => ENVIRONMENT_TEMPLATES.filter((e) => e.agentType !== "twin_backed"),
    [],
  );
  const twinTemplates = useMemo(
    () => ENVIRONMENT_TEMPLATES.filter((e) =>
      e.agentType === "twin_backed"
      && (e.twinBacking?.services?.length || 0) === 1,
    ),
    [],
  );
  const source = tab === "mine"
    ? mine
    : (kind === "twins" ? twinTemplates : regularTemplates);

  const filtered = useMemo(
    () =>
      source.filter((e) => {
        if (tab === "templates" && kind === "regular" && group !== "all" && getAgentType(e.agentType)?.group !== group) return false;
        if (tab === "mine" && backing !== "all") {
          const hasTwin = !!state.byEnv?.[e.id]?.twinBacking;
          if (backing === "twin" && !hasTwin) return false;
          if (backing === "generated" && hasTwin) return false;
        }
        if (query) {
          const q = query.toLowerCase();
          const hay = `${e.name} ${e.tagline} ${e.description}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      }),
    [source, group, query, backing, tab, kind, state.byEnv],
  );

  const twinCount = useMemo(
    () => mine.filter((e) => !!state.byEnv?.[e.id]?.twinBacking).length,
    [mine, state.byEnv],
  );

  // Keep the selection inside the filtered set, or the right pane would show a
  // template the list no longer offers.
  useEffect(() => {
    if (tab !== "templates") return;
    if (!filtered.some((e) => e.id === selectedId)) setSelectedId(filtered[0]?.id);
  }, [filtered, selectedId, tab]);

  const selected = filtered.find((e) => e.id === selectedId) || filtered[0];

  /**
   * Templates and My environments are different collections, so a filter that
   * made sense in one can silently empty the other. Switching tabs clears it.
   */
  const changeTab = (next) => {
    setTab(next);
    setGroup("all");
    setQuery("");
    setBacking("all");
    setKind("regular");
    setSelectedId(null);
  };

  const openEnv = (env) =>
    navigate(paths.dashboard.simulate.environmentDetail(env.id));

  const availableGroups = useMemo(
    () =>
      AGENT_TYPE_GROUPS.filter((g) =>
        source.some((e) => getAgentType(e.agentType)?.group === g),
      ),
    [source],
  );

  return (
    <Box sx={{ p: 2 }}>
      {/* ── header ── */}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ mb: 2.5 }}
      >
        <Box>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Environments</Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            Pick the world your agent will be tested in. Every environment ships with
            seeded data, tools and scenario packs.
          </Typography>
        </Box>
        {/*
          The primary action on the page, so it is filled. `color="primary"`
          rather than the default inherit: the theme's primary is brand purple
          in light and monochrome #FAFAFA in dark, which is the intent in both.
        */}
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          <Button
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:add-circle-linear" width={18} />}
            onClick={() => navigate(paths.dashboard.simulate.environmentNew)}
            sx={{ typography: "s1", fontWeight: 600 }}
          >
            Build from your agent
          </Button>
        </Stack>
      </Stack>

      {/* ── tabs ── */}
      <CustomTabs
        value={tab}
        onChange={(_, v) => changeTab(v)}
        sx={{
          borderBottom: "1px solid", borderColor: "divider", mb: 2, minHeight: 40,
          "& .MuiTab-root": { typography: "s1" },
        }}
      >
        <Tab value="templates" label={`Templates (${regularTemplates.length + twinTemplates.length})`} sx={{ minHeight: 40 }} />
        <Tab value="mine" label={`My environments (${mine.length})`} sx={{ minHeight: 40 }} />
      </CustomTabs>

      {/* ── Worlds / Twins sub-toggle inside Templates ── */}
      {tab === "templates" && (
        <Box sx={{ mb: 2 }}>
          <SegmentedTabs value={kind} onChange={(_, v) => { setKind(v); setSelectedId(null); }}>
            <Tab value="regular" label={`Worlds (${regularTemplates.length})`} />
            <Tab value="twins" label={`Clones (${twinTemplates.length})`} />
          </SegmentedTabs>
        </Box>
      )}

      {/* ── filters ── (hidden in twins mode — composer owns its own search) */}
      {!(tab === "templates" && kind === "twins") && (
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 2.5 }}>
        <TextField
          size="small"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search environments"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="solar:magnifer-linear" width={15} sx={{ color: "text.subtitle" }} />
              </InputAdornment>
            ),
          }}
          sx={{ width: 240, ...compactInputSx }}
        />
        {(tab === "mine" || (tab === "templates" && kind === "regular")) && (
          <TextField
            select
            size="small"
            value={group}
            onChange={(e) => setGroup(e.target.value)}
            SelectProps={{ native: true }}
            sx={{ width: 200, ...compactInputSx }}
          >
            <option value="all">All environment types</option>
            {availableGroups.map((g) => (
              <option key={g} value={g}>{g}</option>
            ))}
          </TextField>
        )}
        {tab === "mine" && twinCount > 0 && (
          <Stack
            direction="row" spacing={0.75}
            sx={{
              p: 0.5, borderRadius: 999, border: "1px solid",
              borderColor: "divider", bgcolor: "background.paper",
            }}
          >
            <BackingChip
              label="All"
              on={backing === "all"}
              onClick={() => setBacking("all")}
              count={mine.length}
            />
            <BackingChip
              label="Clone-backed"
              icon="solar:server-square-linear"
              on={backing === "twin"}
              onClick={() => setBacking("twin")}
              count={twinCount}
              accent
            />
            <BackingChip
              label="Generated"
              on={backing === "generated"}
              onClick={() => setBacking("generated")}
              count={mine.length - twinCount}
            />
          </Stack>
        )}
        <Box flex={1} />
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {filtered.length} {filtered.length === 1 ? "environment" : "environments"}
        </Typography>
      </Stack>
      )}

      {loading ? (
        <Box sx={cardGrid(440)}>
          {Array.from({ length: 6 }).map((_, i) => (
            <EnvironmentCardSkeleton key={i} />
          ))}
        </Box>
      ) : (tab === "templates" && kind === "twins") ? (
        /*
          Twins tab always renders the composer — the "no results"
          empty state doesn't apply here because the composer is
          keyed off its own picker, not the filtered template list.
        */
        <TwinComposer embedded />
      ) : filtered.length === 0 ? (
        <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5 }}>
          <EmptyState
            icon={tab === "mine" ? "solar:bookmark-linear" : "solar:magnifer-linear"}
            title={tab === "mine" ? "No environments yet" : "Nothing matches those filters"}
            body={
              tab === "mine"
                ? "Set up a template and it will appear here, ready to configure and run."
                : "Try a different environment type or clear the search."
            }
            action={
              tab === "mine" ? (
                <Button variant="contained" color="primary" size="small" onClick={() => changeTab("templates")}>
                  Browse templates
                </Button>
              ) : (
                <Button size="small" onClick={() => { setQuery(""); setGroup("all"); }}>
                  Clear filters
                </Button>
              )
            }
          />
        </Box>
      ) : tab === "mine" ? (
        /*
          Already set up — a table rather than a card grid. Cards worked when
          every field was in the same place; once environments started
          carrying build status, agent type and derived counts, aligning them
          in columns is what makes the list scannable.
        */
        <MyEnvironmentsTable envs={filtered} onOpen={openEnv} />
      ) : kind === "twins" ? (
        /*
          Twins tab: full-width composer. Selecting one twin still
          works — the composer treats N=1 as a valid composition — so
          there's no separate "single-twin template" path to maintain.
        */
        <TwinComposer embedded />
      ) : (
        // ── catalogue left, setup right — even split ──
        <Stack direction={{ xs: "column", lg: "row" }} spacing={2} alignItems="flex-start">
          <Box sx={{ flex: 1, minWidth: 0, width: "100%" }}>
            {groupByAgentGroup(filtered).map((g) => (
              <Box key={g.id} sx={{ mb: 3 }}>
                <Typography
                  sx={{
                    typography: "s3", fontWeight: 700, color: "text.primary",
                    textTransform: "uppercase", letterSpacing: .5, mb: 0.375,
                  }}
                >
                  {g.label}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 1.25 }}>
                  {g.blurb}
                </Typography>
                <Stack spacing={1}>
                  {g.items.map((env) => (
                    <TemplateRow
                      key={env.id}
                      env={env}
                      selected={selected?.id === env.id}
                      onSelect={() => setSelectedId(env.id)}
                    />
                  ))}
                </Stack>
              </Box>
            ))}
          </Box>

          <Box
            sx={{
              flex: 1,
              minWidth: 0,
              width: "100%",
              position: { lg: "sticky" },
              top: { lg: 16 },
            }}
          >
            <TemplateSetupPanel env={selected} />
          </Box>
        </Stack>
      )}
    </Box>
  );
}

/* ── one template in the catalogue ───────────────────────────────────────── */

function TemplateRow({ env, selected, onSelect }) {
  const surface = getSurface(env.surface);

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      onClick={onSelect}
      sx={{
        p: 1.5, borderRadius: 1.25, cursor: "pointer",
        border: "1px solid",
        borderColor: selected ? "primary.main" : "divider",
        bgcolor: (t) => selected
          ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.12 : 0.05)
          : "background.paper",
        transition: "border-color .15s ease, background-color .15s ease",
        "&:hover": { borderColor: selected ? undefined : "text.disabled" },
      }}
    >
      <Box
        sx={{
          width: 30, height: 30, borderRadius: 0.875, flexShrink: 0,
          display: "grid", placeItems: "center",
          color: "text.secondary",
          bgcolor: "background.neutral",
        }}
      >
        <Iconify icon={surface.icon} width={16} />
      </Box>

      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{env.name}</Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {env.tagline}
        </Typography>
      </Box>

      {selected && (
        <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: "primary.main", flexShrink: 0 }} />
      )}
    </Stack>
  );
}

TemplateRow.propTypes = {
  env: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onSelect: PropTypes.func,
};

/**
 * Segmented chip in the My-envs filter row. Pill styling matches the
 * existing SegmentedTabs so it visually reads as a group of related
 * choices, not three loose buttons. The twin option gets a subtle
 * purple accent when active — same purple used throughout the twin
 * feature — so the "there's twin-backed stuff here" signal is
 * consistent across the surface.
 */
function BackingChip({ label, icon, on, onClick, count, accent }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.75}
      onClick={onClick}
      sx={{
        px: 1.25, py: 0.5, borderRadius: 999, cursor: "pointer",
        typography: "s3", fontWeight: 700,
        color: on
          ? (accent ? "#7857FC" : "text.primary")
          : "text.subtitle",
        bgcolor: (t) => on
          ? (accent
              ? alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1)
              : alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.06))
          : "transparent",
        transition: "background-color .12s ease, color .12s ease",
        "&:hover": on ? undefined : {
          color: "text.primary",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
        },
      }}
    >
      {icon && <Iconify icon={icon} width={12} />}
      <span>{label}</span>
      <Typography sx={{
        typography: "s3", fontWeight: 700, opacity: 0.7,
        fontVariantNumeric: "tabular-nums",
      }}>{count}</Typography>
    </Stack>
  );
}
BackingChip.propTypes = {
  label: PropTypes.string.isRequired,
  icon: PropTypes.string,
  on: PropTypes.bool,
  onClick: PropTypes.func,
  count: PropTypes.number,
  accent: PropTypes.bool,
};
