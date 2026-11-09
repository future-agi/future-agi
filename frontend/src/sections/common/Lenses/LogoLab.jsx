// F's logo check (`/dashboard/develop/r4-lab?logos=1`, kept by lane M): every
// SourceLogo at 16, 20, 24 and 32 px (walk it light and dark) to check that
// no logo is broken, wrong, blurry or off-size, and the run-source badge a
// run chip shows for each kind and for brands that share a mark.
import React from "react";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import {
  CATALOG,
  GROUP_LABELS,
  RUN_SOURCE_CARDS,
} from "src/sections/common/RunSources/catalog";
import { brandLogoOf } from "src/sections/common/RunSources/brandLogos";
import BundledIcon from "src/sections/common/RunSources/BundledIcon";
import RunSourceBadge from "src/sections/common/Runs/RunSourceBadge";

// One run per generic kind, then branded runs (the two the coding harness products share
// the spark, so their badges keep the name).
const BADGE_RUNS = [
  { kind: "prompt" },
  { kind: "agent" },
  { kind: "code" },
  { kind: "traces" },
  { kind: "harness", harness: { id: "custom_command" } },
  { kind: "http" },
  { kind: "upload" },
  { kind: "harness", harness: { id: "claude_code", name: "Claude Code" } },
  { kind: "code", framework: { id: "claude_agent_sdk" } },
  { kind: "harness", harness: { id: "codex_cli", name: "Codex CLI" } },
  { kind: "code", framework: { id: "langgraph" } },
].map((config, i) => ({ id: `badge-${i}`, name: config.kind, config }));

function BadgeRow() {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      flexWrap="wrap"
      data-testid="logolab-badges"
    >
      <Typography typography="s2" fontWeight="fontWeightSemiBold">
        Run chips
      </Typography>
      {BADGE_RUNS.map((r) => (
        <Box
          key={r.id}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            px: 0.75,
            py: 0.25,
            display: "inline-flex",
          }}
        >
          <RunSourceBadge run={r} />
        </Box>
      ))}
    </Stack>
  );
}

/** What a cell's logo is: the bundled mark (and its colour mode) or a monogram. */
const logoCaption = (e) => {
  const m = brandLogoOf(e.logo?.mark ?? e.id);
  if (!m) return `monogram ${e.logo?.monogram ?? ""}`;
  const mode = m.mode === "mono" ? `mono ${e.logo?.color ?? "text"}` : m.mode;
  return `mark:${m.id} · ${mode}`;
};

function LogoGrid() {
  const groups = Object.entries(CATALOG);
  return (
    <Stack gap={2}>
      <Stack direction="row" gap={1.5} flexWrap="wrap">
        {RUN_SOURCE_CARDS.map((c) => (
          <Stack
            key={c.id}
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              px: 1,
              py: 0.5,
            }}
          >
            <BundledIcon icon={c.icon} width={20} />
            <Typography typography="s2">{c.title}</Typography>
            <Stack direction="row" gap={0.5}>
              {c.logoIds.map((id) => (
                <SourceLogo key={id} entryId={id} size={16} tooltip />
              ))}
            </Stack>
            {c.more ? (
              <Typography
                typography="s3"
                color="text.secondary"
              >{`+${c.more}`}</Typography>
            ) : null}
          </Stack>
        ))}
      </Stack>
      {groups.map(([key, entries]) => (
        <Box key={key}>
          <Typography
            typography="s2"
            fontWeight="fontWeightSemiBold"
            sx={{ mb: 0.5 }}
          >
            {key}
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))",
              gap: 0.75,
            }}
          >
            {entries.map((e) => (
              <Stack
                key={e.id}
                direction="row"
                alignItems="center"
                gap={1}
                data-logo-cell={e.id}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  px: 1,
                  py: 0.5,
                  minWidth: 0,
                }}
              >
                <SourceLogo entry={e} size={16} />
                <SourceLogo entry={e} size={20} />
                <SourceLogo entry={e} size={24} />
                <SourceLogo entry={e} size={32} />
                <Stack sx={{ minWidth: 0 }}>
                  <Typography typography="s2" noWrap>
                    {e.name}
                  </Typography>
                  <Typography typography="s3" color="text.disabled" noWrap>
                    {logoCaption(e)} · {GROUP_LABELS[e.group] ?? e.group}
                  </Typography>
                </Stack>
              </Stack>
            ))}
          </Box>
        </Box>
      ))}
    </Stack>
  );
}

export default function LogoLab() {
  const theme = useTheme();
  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography
        typography="m3"
        fontWeight="fontWeightSemiBold"
        component="h1"
      >
        Run-source logos
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {`Every catalog logo at 16, 20, 24 and 32 px (${theme.palette.mode} theme). Each is the brand's own mark, bundled (sources and licences in brandLogos.js); mono marks follow the theme, colour marks keep their colours. Nothing loads from the network.`}
      </Typography>
      <Box data-logo-theme={theme.palette.mode}>
        <Stack gap={2}>
          <BadgeRow />
          <LogoGrid />
        </Stack>
      </Box>
    </Box>
  );
}
