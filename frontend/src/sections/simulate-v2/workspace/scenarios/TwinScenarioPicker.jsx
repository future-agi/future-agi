import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Checkbox, Chip, TextField, InputAdornment, Tab,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { SectionCard } from "../../components/primitives";
import { proposedScenariosForBacking, twinById } from "../../_mock/twins";

const TWIN_TINT = "#7857FC";

/**
 * The fifth Add Scenarios route — twin-aware scenario suggestions.
 *
 * When the env has a twin backing, we can propose scenarios that
 * *exercise the twin* the way a real support / RevOps / PM copilot
 * would use those services. Two shapes:
 *
 *   · Single-service   — deeper coverage of one service beyond the
 *                        starter pack (community stewardship for
 *                        Slack, doc hygiene for Notion, etc.)
 *
 *   · Cross-service    — the reason twins are interesting in the
 *                        first place: Slack → Notion, Gmail →
 *                        Salesforce, GH → Slack + Linear. Scenarios
 *                        the agent can only pass if it can chain
 *                        across real services.
 *
 * Both kinds live in the same library; the shape is inferred from
 * the number of services they name. Rows already existing on the
 * env are shown as "already added" so the same click doesn't produce
 * a duplicate — mirrors the pattern in TemplatePicker.
 */
export default function TwinScenarioPicker({ env, envState, selected, onAdd }) {
  const backing = envState?.twinBacking;
  const all = useMemo(() => proposedScenariosForBacking(backing), [backing]);
  const existingIds = useMemo(() => new Set((selected || []).map((s) => s.id)), [selected]);
  const [shape, setShape] = useState("all");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState({});

  const shown = all.filter((row) => {
    if (shape === "single" && row.kind !== "single") return false;
    if (shape === "combo" && row.kind !== "combo") return false;
    if (query) {
      const hay = `${row.title} ${row.task} ${row.useCase || ""}`.toLowerCase();
      if (!hay.includes(query.toLowerCase())) return false;
    }
    return true;
  });

  const pickedRows = shown.filter((r) => picked[r.id] && !existingIds.has(r.id));
  const canAdd = pickedRows.length > 0;

  const toggle = (id) => setPicked((prev) => ({ ...prev, [id]: !prev[id] }));

  const add = () => {
    onAdd(pickedRows);
    setPicked({});
  };

  const groupCount = {
    all: all.length,
    single: all.filter((r) => r.kind === "single").length,
    combo: all.filter((r) => r.kind === "combo").length,
  };

  if (!backing) {
    return (
      <SectionCard sx={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <Box sx={{ p: 4, textAlign: "center", flex: 1, display: "grid", placeItems: "center" }}>
          <Box>
            <Iconify icon="solar:server-square-linear" width={30} sx={{ color: "text.subtitle", mb: 1 }} />
            <Typography sx={{ typography: "s1", fontWeight: 700, mb: 0.5 }}>
              This route needs a clone-backed environment
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle", maxWidth: 480, mx: "auto" }}>
              Clone-aware scenarios are generated from the services your env is backed by (Slack, Notion, …).{" "}
              Create the env from a service clone to unlock this route.
            </Typography>
          </Box>
        </Box>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="Clone-aware scenarios"
      subtitle={
        <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap">
          <Typography component="span" sx={{ typography: "s2", color: "text.subtitle" }}>
            Generated from your clone backing —
          </Typography>
          {backing.services.map((sId) => {
            const t = twinById(sId);
            return (
              <Chip
                key={sId} size="small"
                icon={<Iconify icon={t?.icon || "solar:server-square-linear"} width={12} sx={{ ml: "6px !important" }} />}
                label={t?.name || sId}
                sx={{
                  height: 20, fontSize: 11, fontWeight: 700,
                  border: "1px solid", borderColor: "divider",
                  bgcolor: "background.paper",
                  color: "text.primary",
                  "& .MuiChip-label": { px: 0.75 },
                }}
              />
            );
          })}
        </Stack>
      }
      sx={{ flex: 1, display: "flex", flexDirection: "column" }}
    >
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <TextField
          size="small" placeholder="Search clone scenarios…"
          value={query} onChange={(e) => setQuery(e.target.value)}
          InputProps={{
            sx: { typography: "s2" },
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.subtitle" }} />
              </InputAdornment>
            ),
          }}
          sx={{ flex: 1, maxWidth: 360 }}
        />
        <SegmentedTabs value={shape} onChange={(_, v) => setShape(v)} sx={{ flexShrink: 0 }}>
          <Tab value="all" label={`All (${groupCount.all})`} />
          <Tab value="single" label={`Single (${groupCount.single})`} />
          <Tab value="combo" label={`Cross-service (${groupCount.combo})`} />
        </SegmentedTabs>
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
          {pickedRows.length} selected
        </Typography>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {shown.length === 0 ? (
          <Box sx={{ p: 4, textAlign: "center" }}>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {all.length === 0
                ? "No clone-aware scenarios for this backing yet."
                : "Nothing matches your filters."}
            </Typography>
          </Box>
        ) : (
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {shown.map((row) => {
              const already = existingIds.has(row.id);
              const on = !!picked[row.id];
              return (
                <Stack
                  key={row.id} direction="row" alignItems="flex-start" spacing={1.5}
                  onClick={() => !already && toggle(row.id)}
                  sx={{
                    px: 2, py: 1.5, cursor: already ? "not-allowed" : "pointer",
                    opacity: already ? 0.55 : 1,
                    bgcolor: on ? (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.08 : 0.04) : "transparent",
                    "&:hover": already ? undefined : { bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.06 : 0.03) },
                  }}
                >
                  <Checkbox
                    size="small" checked={on || already} disabled={already} disableRipple
                    sx={{
                      p: 0, mt: "1px", flexShrink: 0,
                      color: "text.disabled",
                      "&.Mui-checked": { color: TWIN_TINT },
                    }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" useFlexGap>
                      <Typography sx={{ typography: "s1", fontWeight: 700, color: "text.primary" }}>
                        {row.title}
                      </Typography>
                      {row.kind === "combo" && (
                        <Chip size="small" label="cross-service" sx={{
                          height: 16, fontSize: 9.5, fontWeight: 700,
                          bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.18 : 0.09),
                          color: TWIN_TINT, letterSpacing: 0.3, textTransform: "uppercase",
                          "& .MuiChip-label": { px: 0.75 },
                        }} />
                      )}
                      {already && (
                        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
                          Already added
                        </Typography>
                      )}
                    </Stack>
                    <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.375 }}>
                      {row.task}
                    </Typography>
                    <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 0.75 }} flexWrap="wrap" useFlexGap>
                      {row.services.map((sId) => {
                        const t = twinById(sId);
                        return (
                          <Chip
                            key={sId} size="small"
                            icon={<Iconify icon={t?.icon || "solar:server-square-linear"} width={11} sx={{ ml: "6px !important" }} />}
                            label={t?.name || sId}
                            sx={{
                              height: 18, fontSize: 10, fontWeight: 700,
                              border: "1px solid", borderColor: "divider",
                              bgcolor: "background.paper",
                              color: "text.primary",
                              "& .MuiChip-label": { px: 0.75 },
                            }}
                          />
                        );
                      })}
                      {row.useCase && (
                        <Typography sx={{ typography: "s3", color: "text.subtitle", ml: 0.5 }}>
                          · {row.useCase}
                        </Typography>
                      )}
                    </Stack>
                  </Box>
                </Stack>
              );
            })}
          </Stack>
        )}
      </Box>

      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {shown.length === 0
            ? "—"
            : `${shown.length} available for this backing · ${existingIds.size ? "already on env dimmed" : "none added yet"}`}
        </Typography>
        <Button
          size="small" variant="contained" color="primary"
          disabled={!canAdd}
          onClick={add}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Add {pickedRows.length || ""} {pickedRows.length === 1 ? "scenario" : "scenarios"}
        </Button>
      </Stack>
    </SectionCard>
  );
}

TwinScenarioPicker.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  selected: PropTypes.array,
  onAdd: PropTypes.func,
};
