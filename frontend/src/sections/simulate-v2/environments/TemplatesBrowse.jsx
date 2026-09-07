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

/**
 * Full-page templates browse.
 *
 * Templates deserved their own screen — inline expansion crammed the whole
 * catalog under the picker and offered no comfortable way back. Here the
 * page owns the viewport, has a real header + back button, and hands off
 * to the existing UseTemplate flow (`/environments/use/:templateId`) when
 * a template is picked. That downstream page already does connect-agent
 * → fit-check → ready, so nothing new is invented on that side.
 *
 * Visual language is deliberately monochrome — a big glyph anchors each
 * card, category headers extend a rule line across the section, and each
 * card carries a stats footer so tiles read differently from one another
 * without leaning on color.
 */
export default function TemplatesBrowse() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

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

  const totalShown = grouped.reduce((n, g) => n + g.items.length, 0);
  const popularityThreshold = useMemo(() => {
    const nums = templates.map((t) => t.popularity || 0).sort((a, b) => b - a);
    /* Top ~30% get the "· popular" typographic marker. Text-only so it
       reads at the same visual weight as the surface label — no color. */
    return nums[Math.floor(nums.length * 0.3)] || Infinity;
  }, [templates]);

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ mb: 3 }}
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
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              Prebuilt worlds with seeded state, tools, and rules. Pick one, then wire your agent — you&apos;ll be running scenarios in under a minute.
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

      {totalShown === 0 && (
        <Typography sx={{ typography: "s2", color: "text.subtitle", py: 6, textAlign: "center" }}>
          No templates match {`"${query}"`}. Try a different search.
        </Typography>
      )}

      <Stack spacing={4}>
        {grouped.map((group) => (
          <Box key={group.id}>
            <CategoryHeader label={group.label} count={group.items.length} blurb={group.blurb} />
            <Box
              sx={{
                mt: 1.75,
                display: "grid",
                gap: 1.5,
                gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "repeat(3, 1fr)" },
              }}
            >
              {group.items.map((t) => (
                <TemplateTile
                  key={t.id}
                  template={t}
                  popular={(t.popularity || 0) >= popularityThreshold}
                  onClick={() => navigate(paths.dashboard.simulate.environmentUseTemplate(t.id))}
                />
              ))}
            </Box>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

/* ── section header — big label + count, rule line runs across ───────── */

function CategoryHeader({ label, count, blurb }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Typography sx={{ typography: "s1", fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>
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

/* ── template tile — big glyph anchor + stat footer ──────────────────── */

function TemplateTile({ template, popular, onClick }) {
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
      spacing={1.5}
      sx={{
        p: 2, borderRadius: 1.5, cursor: "pointer",
        border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
        minHeight: 176,
        position: "relative",
        overflow: "hidden",
        transition: "border-color .12s ease, transform .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.4) : th.palette.text.primary,
          transform: "translateY(-1px)",
        },
        "&:hover .tile-arrow": { opacity: 1, transform: "translateX(0)" },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ position: "relative" }}>
        <Iconify
          icon={SURFACE_ICON[template.surface] || "solar:widget-linear"}
          width={12}
          sx={{ color: "text.subtitle" }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontWeight: 700,
            letterSpacing: 0.5,
            textTransform: "uppercase",
          }}
        >
          {template.surface}
        </Typography>
        {popular && (
          <>
            <Box sx={{ color: "text.disabled", fontSize: 10, lineHeight: 1 }}>·</Box>
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontWeight: 700,
                letterSpacing: 0.5,
                textTransform: "uppercase",
              }}
            >
              Popular
            </Typography>
          </>
        )}
      </Stack>

      <Box sx={{ position: "relative", flex: 1 }}>
        <Typography sx={{ typography: "m2", fontWeight: 700, lineHeight: 1.2 }}>
          {template.name}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, lineHeight: 1.45 }}>
          {template.tagline}
        </Typography>
      </Box>

      {statLine && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            position: "relative",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              fontWeight: 600,
              flex: 1,
              minWidth: 0,
              fontVariantNumeric: "tabular-nums",
            }}
            noWrap
          >
            {statLine}
          </Typography>
          <Iconify
            icon="solar:arrow-right-linear"
            width={14}
            className="tile-arrow"
            sx={{
              color: "text.primary",
              opacity: 0,
              transform: "translateX(-4px)",
              transition: "opacity .15s ease, transform .15s ease",
            }}
          />
        </Stack>
      )}
    </Stack>
  );
}
TemplateTile.propTypes = { template: PropTypes.object, popular: PropTypes.bool, onClick: PropTypes.func };

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
