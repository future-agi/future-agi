import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, IconButton, Tooltip, TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { ENVIRONMENT_TEMPLATES, groupByAgentGroup } from "../_mock/environments";
import { packStats } from "../_mock/scenarios";
import TemplateBuildPanel from "./TemplateBuildPanel";

/**
 * Templates browser — master/detail.
 *
 * The catalog lives on the left; picking a template reveals its build panel on
 * the right (Build here / Build locally) without leaving the page. A template
 * is a world that already exists, so there's nothing to configure before
 * building — which is exactly why the old separate "use template" screen was
 * one hop too many and now folds in here as the detail pane.
 */
export default function TemplatesBrowse() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(null);

  const templates = useMemo(
    () => ENVIRONMENT_TEMPLATES.filter((t) => t.agentType !== "twin_backed"),
    [],
  );

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? templates.filter((t) => `${t.name} ${t.tagline} ${t.description || ""}`.toLowerCase().includes(q))
      : templates;
    return groupByAgentGroup(filtered);
  }, [templates, query]);

  const flat = useMemo(() => grouped.flatMap((g) => g.items), [grouped]);
  /* Nothing is selected by default — the catalog opens full-width, and the
     build panel only appears once a template is picked. A search that filters
     out the current selection collapses back to the full-width gallery. */
  const selected = flat.find((t) => t.id === selectedId) || null;

  const popularityThreshold = useMemo(() => {
    const nums = templates.map((t) => t.popularity || 0).sort((a, b) => b - a);
    return nums[Math.floor(nums.length * 0.3)] || Infinity;
  }, [templates]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* ── header ── */}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ px: 2, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Stack direction="row" alignItems="flex-start" spacing={1.5} flex={1} minWidth={0}>
          <Tooltip arrow title="Back to how you want to start">
            <IconButton size="small" onClick={() => navigate(paths.dashboard.simulate.environments)} sx={{ mt: 0.25 }}>
              <Iconify icon="solar:alt-arrow-left-linear" width={18} />
            </IconButton>
          </Tooltip>
          <Box>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Use our template
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Prebuilt worlds with seeded state, tools, and rules. Pick one, then build it — here or locally.
            </Typography>
          </Box>
        </Stack>
        <TextField
          size="small"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search templates…"
          InputProps={{
            startAdornment: <Iconify icon="solar:magnifer-linear" width={13} sx={{ color: "text.subtitle", mr: 0.75 }} />,
          }}
          sx={{ width: { sm: 280 }, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2" } }}
        />
      </Stack>

      {/* ── catalog (full-width) → master/detail once a template is picked ── */}
      <Box
        sx={{
          flex: 1, minHeight: 0,
          display: "grid",
          gridTemplateColumns: selected
            ? { xs: "1fr", lg: "minmax(0, 1fr) minmax(400px, 480px)" }
            : "1fr",
        }}
      >
        {/* master — the catalog */}
        <Box sx={{ minWidth: 0, overflow: "auto", p: 2, borderRight: selected ? { lg: "1px solid" } : "none", borderColor: { lg: "divider" } }}>
          {flat.length === 0 ? (
            <Typography sx={{ typography: "s2", color: "text.subtitle", py: 6, textAlign: "center" }}>
              No templates match {`"${query}"`}. Try a different search.
            </Typography>
          ) : (
            <Stack spacing={selected ? 3 : 4}>
              {grouped.map((group) => (
                <Box key={group.id}>
                  <CategoryHeader label={group.label} count={group.items.length} blurb={group.blurb} />
                  <Box
                    sx={{
                      mt: 1.5,
                      display: "grid",
                      gap: 1.25,
                      /* Roomy 3-up gallery until a template is selected; the
                         master column then narrows to two. */
                      gridTemplateColumns: selected
                        ? { xs: "1fr", md: "1fr 1fr" }
                        : { xs: "1fr", sm: "1fr 1fr", md: "repeat(3, 1fr)" },
                    }}
                  >
                    {group.items.map((t) => (
                      <TemplateRow
                        key={t.id}
                        template={t}
                        popular={(t.popularity || 0) >= popularityThreshold}
                        selected={selected?.id === t.id}
                        onClick={() => setSelectedId(t.id)}
                      />
                    ))}
                  </Box>
                </Box>
              ))}
            </Stack>
          )}
        </Box>

        {/* detail — build panel, only once a template is picked */}
        {selected && (
          <Box sx={{ minWidth: 0, overflow: "auto", p: 2 }}>
            <TemplateBuildPanel key={selected.id} template={selected} showName />
          </Box>
        )}
      </Box>
    </Box>
  );
}

/* ── section header — label + count, rule line runs across ───────── */

function CategoryHeader({ label, count, blurb }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Typography sx={{ typography: "s2", fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" }}>
          {label}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 700,
            color: "text.subtitle",
            fontVariantNumeric: "tabular-nums",
            letterSpacing: 0.2,
          }}
        >
          {String(count).padStart(2, "0")}
        </Typography>
        <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
      </Stack>
      {blurb && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {blurb}
        </Typography>
      )}
    </Box>
  );
}
CategoryHeader.propTypes = { label: PropTypes.string, count: PropTypes.number, blurb: PropTypes.string };

/* ── compact template row — selectable master item ──────────────────── */

function TemplateRow({ template, popular, selected, onClick }) {
  const stats = useMemo(() => packStats(template), [template]);
  const rows = useMemo(
    () => (template.seed?.tables || []).reduce((a, t) => a + (t.rows || 0), 0),
    [template],
  );
  const toolCount = template.tools?.length || 0;

  const statLine = [
    stats.scenarios > 0 && `${formatCount(stats.scenarios)} scenario${stats.scenarios === 1 ? "" : "s"}`,
    toolCount > 0 && `${toolCount} tool${toolCount === 1 ? "" : "s"}`,
    rows > 0 && `${formatCount(rows)} row${rows === 1 ? "" : "s"}`,
  ].filter(Boolean).join(" · ");

  return (
    <Stack
      onClick={onClick}
      role="button"
      spacing={0.75}
      sx={{
        p: 1.5, borderRadius: 1.25, cursor: "pointer",
        border: "1px solid",
        borderColor: (th) => (selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.5) : th.palette.primary.main)
          : th.palette.divider),
        bgcolor: (th) => (selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.06) : alpha(th.palette.primary.main, 0.04))
          : "background.paper"),
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => (selected
            ? undefined
            : (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.35) : th.palette.text.primary)),
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Iconify icon={SURFACE_ICON[template.surface] || "solar:widget-linear"} width={11} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" }}>
          {template.surface}
        </Typography>
        {popular && (
          <>
            <Box sx={{ color: "text.disabled", fontSize: 10, lineHeight: 1 }}>·</Box>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" }}>
              Popular
            </Typography>
          </>
        )}
      </Stack>

      <Typography noWrap sx={{ typography: "s1", fontWeight: 700, lineHeight: 1.2 }}>
        {template.name}
      </Typography>
      <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
        {template.tagline}
      </Typography>

      {statLine && (
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontWeight: 600, fontVariantNumeric: "tabular-nums", pt: 0.25 }}>
          {statLine}
        </Typography>
      )}
    </Stack>
  );
}
TemplateRow.propTypes = { template: PropTypes.object, popular: PropTypes.bool, selected: PropTypes.bool, onClick: PropTypes.func };

/* ── helpers ─────────────────────────────────────────────────────────── */

function formatCount(n) {
  if (n >= 1000) {
    const k = n / 1000;
    return `${k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}k`;
  }
  return String(n);
}

const SURFACE_ICON = {
  voice: "solar:phone-linear",
  chat: "solar:chat-round-linear",
  code: "solar:code-linear",
  api: "solar:cloud-linear",
};
