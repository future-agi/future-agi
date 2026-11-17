import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tab, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { useAdoptTemplate } from "src/api/simulate-environments/environments";
import { packStats } from "../helpers/packStats";
import SectionCard from "../components/SectionCard";
import LocalScaffoldCard from "./LocalScaffoldCard";
import TemplateDraftPreview from "./TemplateDraftPreview";
import {
  AGENT_STAT_VALUE,
  BUILD_MODES,
  BUILD_MODE_TABS,
  DRAFT_BULLETS,
  DRAFT_CARD,
  LOCAL_DRAFT_NOTICE,
  STATS_CARD,
  TEMPLATE_ADOPT_LABEL,
  TEMPLATE_SHAPE,
  TEMPLATE_STAT_COPY,
  surfaceIconFor,
} from "../useTemplate.constants";

/**
 * Inspect a library template locally or show its CLI scaffold instructions.
 * A preview seeds draft data only: no sandbox, server id or workspace route.
 * Used by both the standalone template page and the inline browse panel.
 */
const sentenceCase = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

export default function TemplateBuildPanel({ template, showName = false }) {
  const [mode, setMode] = useState(BUILD_MODES.PREVIEW);
  const adopt = useAdoptTemplate();

  if (!template) return null;

  const rows = (template.seed?.tables || []).reduce(
    (a, t) => a + (t.rows || 0),
    0,
  );
  const stats = [
    { label: "World", value: TEMPLATE_STAT_COPY.world(rows) },
    {
      label: "Tools",
      value: TEMPLATE_STAT_COPY.tools(template.tools?.length || 0),
    },
    {
      label: "Hard rules",
      value: TEMPLATE_STAT_COPY.rules(template.rules?.length || 0),
    },
    {
      label: "Scenarios",
      value: TEMPLATE_STAT_COPY.scenarios(packStats(template).scenarios),
    },
    { label: "Evals", value: `${template.evalPreset?.length || 0} suggested` },
    { label: "Agent", value: AGENT_STAT_VALUE },
  ];
  // Do not register this draft as an environment or open the backend workspace.
  // That route has server reads and run controls, neither of which this local
  // draft can satisfy. Ignore a prior template's draft after a route change.
  const draft = adopt.data?.templateId === template.id ? adopt.data : null;

  return (
    <Stack spacing={2}>
      {showName && (
        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify
              icon={surfaceIconFor(template.surface)}
              width={13}
              sx={{ color: "text.subtitle" }}
            />
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", fontWeight: 500 }}
            >
              {sentenceCase(template.surface || "")}
            </Typography>
          </Stack>
          <Typography sx={{ typography: "m1", fontWeight: 500, mt: 0.5 }}>
            {template.name}
          </Typography>
          <Typography
            sx={{ typography: "s1", color: "text.secondary", mt: 0.25 }}
          >
            {template.tagline}
          </Typography>
        </Box>
      )}

      <SegmentedTabs
        value={mode}
        onChange={(_e, next) => setMode(next)}
        sx={{ alignSelf: "flex-start" }}
      >
        {BUILD_MODE_TABS.map((t) => (
          <Tab
            key={t.value}
            value={t.value}
            label={
              <Stack
                component="span"
                direction="row"
                alignItems="center"
                spacing={0.75}
              >
                <Iconify icon={t.icon} width={13} />
                <Box component="span">{t.label}</Box>
              </Stack>
            }
          />
        ))}
      </SegmentedTabs>

      {mode === BUILD_MODES.PREVIEW && (
        <>
          <SectionCard title={DRAFT_CARD.title} subtitle={DRAFT_CARD.subtitle}>
            <Stack sx={{ p: 1.5 }} spacing={1.5}>
              {DRAFT_BULLETS.map((row) => (
                <Stack
                  key={row.text}
                  direction="row"
                  spacing={1.25}
                  alignItems="flex-start"
                >
                  <Iconify
                    icon={row.icon}
                    width={16}
                    sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
                  />
                  <Typography
                    sx={{ typography: "s1", color: "text.secondary" }}
                  >
                    {row.text}
                  </Typography>
                </Stack>
              ))}
            </Stack>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 1.5,
                py: 1.5,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Button
                variant="contained"
                color="primary"
                onClick={() => adopt.mutate(template)}
                disabled={adopt.isPending}
                startIcon={
                  <Iconify icon="solar:document-text-linear" width={16} />
                }
                sx={{ whiteSpace: "nowrap" }}
              >
                {TEMPLATE_ADOPT_LABEL}
              </Button>
            </Stack>
          </SectionCard>

          <Box
            sx={{
              p: 1.5,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Stack direction="row" spacing={1.25} alignItems="flex-start">
              <Iconify
                icon={LOCAL_DRAFT_NOTICE.icon}
                width={16}
                sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
              />
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                <Box
                  component="span"
                  sx={{ fontWeight: 500, color: "text.primary" }}
                >
                  {LOCAL_DRAFT_NOTICE.heading}
                </Box>{" "}
                {LOCAL_DRAFT_NOTICE.body}
              </Typography>
            </Stack>
          </Box>

          {draft && (
            <TemplateDraftPreview draft={draft} onClose={adopt.reset} />
          )}

          <SectionCard title={STATS_CARD.title} subtitle={STATS_CARD.subtitle}>
            <Stack sx={{ px: 1.5, py: 1.5 }} spacing={1.25}>
              {stats.map((s) => (
                <Stack key={s.label} direction="row" spacing={2}>
                  <Typography
                    sx={{
                      typography: "s1",
                      color: "text.subtitle",
                      width: 96,
                      flexShrink: 0,
                    }}
                  >
                    {s.label}
                  </Typography>
                  <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                    {s.value}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </>
      )}

      {mode === BUILD_MODES.LOCAL && <LocalScaffoldCard template={template} />}
    </Stack>
  );
}
TemplateBuildPanel.propTypes = {
  template: TEMPLATE_SHAPE,
  showName: PropTypes.bool,
};
