import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Chip,
  TextField,
  InputAdornment,
  Tab,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { SectionCard } from "../../components/primitives";
import { proposedScenariosForBacking, twinById } from "../../_mock/twins";
import { clickable, focusRing } from "../../components/clickable";

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
  const existingIds = useMemo(
    () => new Set((selected || []).map((s) => s.id)),
    [selected],
  );
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

  const pickedRows = shown.filter(
    (r) => picked[r.id] && !existingIds.has(r.id),
  );
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
        <Box
          sx={{
            p: 4,
            textAlign: "center",
            flex: 1,
            display: "grid",
            placeItems: "center",
          }}
        >
          <Box>
            <Iconify
              icon="solar:server-square-linear"
              width={30}
              sx={{ color: "text.tertiary", mb: 1 }}
            />
            <Typography sx={{ typography: "s1", fontWeight: 600, mb: 0.5 }}>
              This needs a sandboxed-apps environment
            </Typography>
            <Typography
              sx={{
                typography: "s2",
                color: "text.tertiary",
                maxWidth: 480,
                mx: "auto",
              }}
            >
              Sandbox scenarios are written from the apps your environment
              sandboxes (Slack, Notion, …). Build the environment as a SaaS
              sandbox to use this route.
            </Typography>
          </Box>
        </Box>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="Sandbox scenarios"
      subtitle={
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          flexWrap="wrap"
        >
          <Typography
            component="span"
            sx={{ typography: "s2", color: "text.tertiary" }}
          >
            Written from your sandboxed apps —
          </Typography>
          {backing.services.map((sId) => {
            const t = twinById(sId);
            return (
              <Chip
                key={sId}
                size="small"
                icon={
                  <Iconify
                    icon={t?.icon || "solar:server-square-linear"}
                    width={12}
                    sx={{ ml: "6px !important" }}
                  />
                }
                label={t?.name || sId}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.paper",
                  color: "text.primary",
                }}
              />
            );
          })}
        </Stack>
      }
      sx={{ flex: 1, display: "flex", flexDirection: "column" }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <TextField
          size="small"
          placeholder="Search sandbox scenarios…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          InputProps={{
            sx: { typography: "s2" },
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifer-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{ flex: 1, maxWidth: 360 }}
        />
        <SegmentedTabs
          value={shape}
          onChange={(_, v) => setShape(v)}
          sx={{ flexShrink: 0 }}
        >
          <Tab value="all" label={`All (${groupCount.all})`} />
          <Tab value="single" label={`Single (${groupCount.single})`} />
          <Tab value="combo" label={`Cross-service (${groupCount.combo})`} />
        </SegmentedTabs>
        <Box flex={1} />
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            whiteSpace: "nowrap",
          }}
        >
          {pickedRows.length} selected
        </Typography>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {shown.length === 0 ? (
          <Box sx={{ p: 4, textAlign: "center" }}>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              {all.length === 0
                ? "No sandbox scenarios for these apps yet."
                : "Nothing matches your filters."}
            </Typography>
          </Box>
        ) : (
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {shown.map((row) => {
              const already = existingIds.has(row.id);
              const on = !!picked[row.id];
              return (
                <Stack
                  key={row.id}
                  direction="row"
                  alignItems="flex-start"
                  spacing={1.5}
                  {...clickable(() => !already && toggle(row.id), {
                    role: "checkbox",
                    checked: on || already,
                    disabled: already,
                  })}
                  sx={{
                    ...focusRing,
                    px: 2,
                    py: 1,
                    cursor: already ? "not-allowed" : "pointer",
                    opacity: already ? 0.55 : 1,
                    bgcolor: on ? "status.discovery.bg" : "transparent",
                    "&:hover": already
                      ? undefined
                      : { bgcolor: "status.discovery.bg" },
                  }}
                >
                  <Checkbox
                    size="small"
                    checked={on || already}
                    disabled={already}
                    disableRipple
                    tabIndex={-1}
                    inputProps={{ "aria-hidden": true }}
                    sx={{
                      p: 0,
                      mt: "1px",
                      flexShrink: 0,
                      color: "text.disabled",
                      "&.Mui-checked": { color: "text.primary" },
                      "&.MuiCheckbox-indeterminate": { color: "text.primary" },
                    }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={0.75}
                      flexWrap="wrap"
                      useFlexGap
                    >
                      <Typography
                        sx={{
                          typography: "s2_1",
                          fontWeight: 500,
                          color: "text.primary",
                        }}
                      >
                        {row.title}
                      </Typography>
                      {row.kind === "combo" && (
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 600,
                            color: "text.tertiary",
                            letterSpacing: 0.4,
                            textTransform: "none",
                          }}
                        >
                          cross-service
                        </Typography>
                      )}
                      {already && (
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 600,
                            color: "text.tertiary",
                            textTransform: "none",
                            letterSpacing: 0.4,
                          }}
                        >
                          Already added
                        </Typography>
                      )}
                    </Stack>
                    <Typography
                      sx={{
                        typography: "s2",
                        color: "text.secondary",
                        mt: 0.375,
                      }}
                    >
                      {row.task}
                    </Typography>
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={0.5}
                      sx={{ mt: 0.75 }}
                      flexWrap="wrap"
                      useFlexGap
                    >
                      {row.services.map((sId) => {
                        const t = twinById(sId);
                        return (
                          <Stack
                            key={sId}
                            direction="row"
                            alignItems="center"
                            spacing={0.5}
                          >
                            <Iconify
                              icon={t?.icon || "solar:server-square-linear"}
                              width={12}
                              sx={{ color: "text.tertiary" }}
                            />
                            <Typography
                              sx={{
                                typography: "s3",
                                fontWeight: 500,
                                color: "text.secondary",
                              }}
                            >
                              {t?.name || sId}
                            </Typography>
                          </Stack>
                        );
                      })}
                      {row.useCase && (
                        <Typography
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            ml: 0.5,
                          }}
                        >
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
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {shown.length === 0
            ? "—"
            : `${shown.length} available for this backing · ${existingIds.size ? "already in the environment dimmed" : "none added yet"}`}
        </Typography>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!canAdd}
          onClick={add}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
        >
          Add {pickedRows.length || ""}{" "}
          {pickedRows.length === 1 ? "scenario" : "scenarios"}
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
