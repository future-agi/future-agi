import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { useNavigate } from "react-router-dom";
import {
  Box, Button, Stack, Tab, Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { usePrebuiltEnvironment } from "src/api/simulate-environments/prebuilt";
import SectionCard from "../components/SectionCard";
import LocalScaffoldCard from "./LocalScaffoldCard";
import {
  AGENT_STAT_VALUE,
  BUILD_MODES,
  BUILD_MODE_TABS,
  CLOUD_BULLETS,
  CLOUD_CARD,
  NOTHING_TOUCHES_PRODUCTION,
  SCENARIOS_CARD,
  STATS_CARD,
  TEMPLATE_ADOPT_LABEL,
  TEMPLATE_SHAPE,
  surfaceIconFor,
} from "../useTemplate.constants";

/**
 * Use a template — the panel shown for one entry in the library.
 *
 * A template is an environment the harness already built and validated, shared
 * by everyone, so there is nothing to derive. Opening it shows the shared
 * environment read-only and creates nothing; the first edit or run there copies
 * it into the account. Rendered as the standalone Use-template page and inline
 * as a detail pane, so it takes a base `template` rather than owning a route.
 */
export default function TemplateBuildPanel({ template, showName = false }) {
  const navigate = useNavigate();
  const [mode, setMode] = useState(BUILD_MODES.CLOUD);
  const detail = usePrebuiltEnvironment(template?.id);

  if (!template) return null;

  const stats = [
    { label: "Tools", value: `${template.tools?.length || 0} the world answers` },
    { label: "Hard rules", value: `${template.rules?.length || 0} graded on every run` },
    { label: "Scenarios", value: `${template.scenarioCount || 0} ready to run` },
    { label: "Evals", value: `${template.evalPreset?.length || 0} selected` },
    { label: "Agent", value: AGENT_STAT_VALUE },
  ];
  const scenarios = detail.data?.scenarios ?? [];

  const handleUse = () =>
    navigate(paths.dashboard.simulate.environments.detail(template.environmentId));

  return (
    <Stack spacing={2}>
      {showName && (
        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify icon={surfaceIconFor(template.surface)} width={13} sx={{ color: "text.subtitle" }} />
            <Typography
              sx={{
                typography: "s3", color: "text.subtitle", fontWeight: "fontWeightBold",
                letterSpacing: 0.5,
              }}
            >
              {(template.surface || "").toUpperCase()}
            </Typography>
          </Stack>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightBold", mt: 0.5 }}>{template.name}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{template.tagline}</Typography>
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
            label={(
              <Stack component="span" direction="row" alignItems="center" spacing={0.75}>
                <Iconify icon={t.icon} width={13} />
                <Box component="span">{t.label}</Box>
              </Stack>
            )}
          />
        ))}
      </SegmentedTabs>

      {mode === BUILD_MODES.CLOUD && (
        <>
          <SectionCard title={CLOUD_CARD.title} subtitle={CLOUD_CARD.subtitle}>
            <Stack sx={{ p: 2.5 }} spacing={1.75}>
              {CLOUD_BULLETS.map((row) => (
                <Stack key={row.text} direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify icon={row.icon} width={16} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>{row.text}</Typography>
                </Stack>
              ))}
            </Stack>
            <Stack
              direction="row" alignItems="center" spacing={1.5}
              sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
            >
              <Button
                variant="contained" color="primary"
                onClick={handleUse}
                startIcon={<Iconify icon="solar:magic-stick-3-bold" width={16} />}
                sx={{ typography: "s2", fontWeight: "fontWeightBold", whiteSpace: "nowrap" }}
              >
                {TEMPLATE_ADOPT_LABEL}
              </Button>
            </Stack>
          </SectionCard>

          <SectionCard title={STATS_CARD.title} subtitle={STATS_CARD.subtitle}>
            <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
              {stats.map((s) => (
                <Stack key={s.label} direction="row" spacing={2}>
                  <Typography sx={{ typography: "s2", color: "text.subtitle", width: 96, flexShrink: 0 }}>
                    {s.label}
                  </Typography>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{s.value}</Typography>
                </Stack>
              ))}
            </Stack>
          </SectionCard>

          {scenarios.length > 0 && (
            <SectionCard title={SCENARIOS_CARD.title} subtitle={SCENARIOS_CARD.subtitle}>
              <Stack sx={{ px: 2.5, py: 2, maxHeight: 360, overflow: "auto" }} spacing={1.5}>
                {scenarios.map((scenario) => (
                  <Box key={scenario.scenario_key}>
                    <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
                      {scenario.name || scenario.scenario_key}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      {[scenario.use_case, scenario.persona?.name].filter(Boolean).join(" · ")}
                    </Typography>
                  </Box>
                ))}
              </Stack>
            </SectionCard>
          )}

          <Box
            sx={{
              p: 2, borderRadius: 1.25, border: "1px solid",
              borderColor: (t) => alpha(t.palette.success.main, 0.3),
              bgcolor: (t) => alpha(t.palette.success.main, t.palette.mode === "dark" ? 0.08 : 0.04),
            }}
          >
            <Stack direction="row" spacing={1.25} alignItems="flex-start">
              <Iconify icon={NOTHING_TOUCHES_PRODUCTION.icon} width={16} sx={{ color: "success.main", flexShrink: 0, mt: "1px" }} />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                <Box component="span" sx={{ fontWeight: "fontWeightBold", color: "text.primary" }}>
                  {NOTHING_TOUCHES_PRODUCTION.heading}
                </Box>{" "}
                {NOTHING_TOUCHES_PRODUCTION.body}
              </Typography>
            </Stack>
          </Box>
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
