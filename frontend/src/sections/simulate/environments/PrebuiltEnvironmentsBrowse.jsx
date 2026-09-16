import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import {
  Box, Stack, Typography, IconButton, TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { usePrebuiltEnvironments } from "src/api/simulate-environments/prebuilt";
import CategoryHeader from "./components/CategoryHeader";
import TemplateTile from "./cards/TemplateTile";
import { groupByAgentGroup } from "./helpers/groupByAgentGroup";

/**
 * Full-page prebuilt environments browse.
 *
 * Templates deserved their own screen — inline expansion crammed the whole
 * catalog under the picker and offered no comfortable way back. Here the
 * page owns the viewport, has a real header + back button.
 *
 * Visual language is deliberately monochrome — a big glyph anchors each
 * card, category headers extend a rule line across the section, and each
 * card carries a stats footer so tiles read differently from one another
 * without leaning on color.
 */
export default function PrebuiltEnvironmentsBrowse() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
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
          <CustomTooltip show arrow size="small" title="Back to how you want to start">
            <IconButton aria-label="Back to how you want to start" size="small" onClick={() => navigate(paths.dashboard.simulate.environments.root)} sx={{ mt: 0.25 }}>
              <Iconify icon="solar:alt-arrow-left-linear" width={18} />
            </IconButton>
          </CustomTooltip>
          <Box>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
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

      {!isLoading && totalShown === 0 && (
        <Typography sx={{ typography: "s2", color: "text.subtitle", py: 6, textAlign: "center" }}>
          {query.trim()
            ? `No templates match "${query}". Try a different search.`
            : "No prebuilt environments yet."}
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
                  // TODO(Phase-3): open the use-template adopt flow
                  onClick={() => enqueueSnackbar("Opening a prebuilt environment lands in a later phase.", { variant: "info" })}
                />
              ))}
            </Box>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
