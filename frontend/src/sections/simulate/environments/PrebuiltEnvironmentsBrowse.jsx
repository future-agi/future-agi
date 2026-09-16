import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Stack, Typography, IconButton, TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { usePrebuiltEnvironments } from "src/api/simulate-environments/prebuilt";
import CategoryHeader from "./components/CategoryHeader";
import TemplateRow from "./cards/TemplateRow";
import TemplateBuildPanel from "./cards/TemplateBuildPanel";
import { groupByAgentGroup } from "./helpers/groupByAgentGroup";
import { BROWSE_COPY } from "./prebuiltEnvironments.constants";

/**
 * Prebuilt environments browse — master/detail.
 *
 * The catalog lives on the left; picking a template reveals its build panel on
 * the right (Build here / Build locally) without leaving the page. A template
 * is a world that already exists, so there's nothing to configure before
 * building — which is why the old separate "use template" screen folded in here
 * as the detail pane. The standalone route stays for deep-links only.
 *
 * Nothing is selected by default (full-width 3-up gallery); the master narrows
 * to 2-up once a template is picked, and a search that filters out the current
 * selection collapses back to the full-width gallery.
 */
export default function PrebuiltEnvironmentsBrowse() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const { data, isLoading } = usePrebuiltEnvironments();

  const templates = useMemo(
    () => (data ?? []).filter((t) => t.agentType !== "twin_backed"),
    [data],
  );

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? templates.filter((t) => `${t.name} ${t.tagline} ${t.description || ""}`.toLowerCase().includes(q))
      : templates;
    return groupByAgentGroup(filtered);
  }, [templates, query]);

  const flat = useMemo(() => grouped.flatMap((g) => g.items), [grouped]);
  const totalShown = flat.length;
  const selected = flat.find((t) => t.id === selectedId) || null;

  const popularityThreshold = useMemo(() => {
    const nums = templates.map((t) => t.popularity || 0).sort((a, b) => b - a);
    /* Top ~30% get the "· popular" typographic marker. Text-only so it
       reads at the same visual weight as the surface label — no color. */
    return nums[Math.floor(nums.length * 0.3)] || Infinity;
  }, [templates]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ px: 2, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Stack direction="row" alignItems="flex-start" spacing={1.5} flex={1} minWidth={0}>
          <CustomTooltip show arrow size="small" title={BROWSE_COPY.backTooltip}>
            <IconButton
              aria-label={BROWSE_COPY.backTooltip}
              size="small"
              onClick={() => navigate(paths.dashboard.simulate.environments.root)}
              sx={{ mt: 0.25 }}
            >
              <Iconify icon="solar:alt-arrow-left-linear" width={18} />
            </IconButton>
          </CustomTooltip>
          <Box>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
              {BROWSE_COPY.title}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {BROWSE_COPY.subtitle}
            </Typography>
          </Box>
        </Stack>
        <TextField
          size="small"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={BROWSE_COPY.searchPlaceholder}
          InputProps={{
            startAdornment: <Iconify icon="solar:magnifer-linear" width={13} sx={{ color: "text.subtitle", mr: 0.75 }} />,
          }}
          sx={{ width: { sm: 280 }, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2" } }}
        />
      </Stack>

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
        <Box
          sx={{
            minWidth: 0, overflow: "auto", p: 2,
            borderRight: selected ? { lg: "1px solid" } : "none",
            borderColor: { lg: "divider" },
          }}
        >
          {!isLoading && totalShown === 0 ? (
            <Typography sx={{ typography: "s2", color: "text.subtitle", py: 6, textAlign: "center" }}>
              {query.trim() ? BROWSE_COPY.noMatch(query) : BROWSE_COPY.emptyLibrary}
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
