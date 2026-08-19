import PropTypes from "prop-types";
import { useMemo, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Tab, InputAdornment, TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { ENVIRONMENT_TEMPLATES, groupByAgentGroup } from "../_mock/environments";
import { AGENT_TYPE_GROUPS, getAgentType } from "../_mock/agentTypes";
import { getSurface } from "../_mock/surfaces";
import { useSimStore } from "../store";
import EnvironmentCard from "./EnvironmentCard";
import TemplateSetupPanel from "./TemplateSetupPanel";
import { EnvironmentCardSkeleton } from "../components/loading";
import { EmptyState, cardGrid } from "../components/primitives";

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
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("all");
  const [selectedId, setSelectedId] = useState(ENVIRONMENT_TEMPLATES[0]?.id);
  const [loading, setLoading] = useState(true);

  // A short shaped load so the skeleton is actually seen — this is the state a
  // real fetch would sit in, and stakeholders should see it.
  useEffect(() => {
    const t = setTimeout(() => setLoading(false), 620);
    return () => clearTimeout(t);
  }, []);

  const mine = state.myEnvironments;
  const source = tab === "mine" ? mine : ENVIRONMENT_TEMPLATES;

  const filtered = useMemo(
    () =>
      source.filter((e) => {
        if (group !== "all" && getAgentType(e.agentType)?.group !== group) return false;
        if (query) {
          const q = query.toLowerCase();
          const hay = `${e.name} ${e.tagline} ${e.description}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      }),
    [source, group, query],
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
        <Button
          variant="outlined"
          startIcon={<Iconify icon="solar:add-circle-linear" width={18} />}
          onClick={() => navigate(paths.dashboard.simulate.environmentNew)}
          sx={{ flexShrink: 0, typography: "s1", fontWeight: 600, color: "text.primary", borderColor: "divider" }}
        >
          Build from scratch
        </Button>
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
        <Tab value="templates" label={`Templates (${ENVIRONMENT_TEMPLATES.length})`} sx={{ minHeight: 40 }} />
        <Tab value="mine" label={`My environments (${mine.length})`} sx={{ minHeight: 40 }} />
      </CustomTabs>

      {/* ── filters ── */}
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
        <Box flex={1} />
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {filtered.length} {filtered.length === 1 ? "environment" : "environments"}
        </Typography>
      </Stack>

      {loading ? (
        <Box sx={cardGrid(440)}>
          {Array.from({ length: 6 }).map((_, i) => (
            <EnvironmentCardSkeleton key={i} />
          ))}
        </Box>
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
        // Already set up — these open straight into the workspace.
        <Box sx={cardGrid(440)}>
          {filtered.map((env) => (
            <EnvironmentCard key={env.id} env={env} onOpen={openEnv} />
          ))}
        </Box>
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
