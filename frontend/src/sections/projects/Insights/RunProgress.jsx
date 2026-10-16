import React from "react";
import PropTypes from "prop-types";
import { Box, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import { keyframes } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { UNITS } from "src/sections/projects/observePopulation";
import { formatCount, formatSpan } from "./insightsFormat";
import { useElapsedSeconds } from "./insightsHooks";
import { STAGES, stageIndex } from "./insightsLenses";

const arrive = keyframes`
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: none; }
`;

function StageRail({ current }) {
  const visible = STAGES.filter((stage) => stage.key !== "done");
  return (
    <Stack
      direction="row"
      component="ol"
      aria-label="Report stages"
      sx={{ listStyle: "none", m: 0, p: 0, gap: 0.5 }}
    >
      {visible.map((stage, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <Stack
            key={stage.key}
            component="li"
            aria-current={active ? "step" : undefined}
            spacing={0.75}
            sx={{ flex: 1, minWidth: 0 }}
          >
            <Box
              sx={{
                height: 4,
                borderRadius: 2,
                bgcolor: done || active ? "primary.main" : "action.hover",
                opacity: active ? 0.55 : 1,
              }}
            />
            <Stack direction="row" spacing={0.5} alignItems="center">
              {done && (
                <Iconify
                  icon="mdi:check"
                  width={14}
                  sx={{ color: "primary.main" }}
                />
              )}
              <Typography
                variant="caption"
                fontWeight={active ? 600 : 400}
                color={done || active ? "text.primary" : "text.secondary"}
                noWrap
              >
                {stage.label}
              </Typography>
            </Stack>
          </Stack>
        );
      })}
    </Stack>
  );
}

StageRail.propTypes = { current: PropTypes.number.isRequired };

export default function RunProgress({ run }) {
  const progress = run.progress || {};
  const stage = progress.stage || run.stage || "queued";
  const current = stageIndex(stage);
  const meta = STAGES[current];
  const plural = (UNITS[run.unit] || UNITS.traces).plural;
  const elapsed = useElapsedSeconds(run.started_at || run.created_at, true);
  const done = Number(progress.done) || 0;
  const total = Number(progress.total) || run.sample_size || 0;
  const counted = stage === "reading" || stage === "summarising";
  const recent = [...(progress.recent || [])].reverse();

  return (
    <Paper variant="outlined" sx={{ p: 3, borderRadius: 1.5 }}>
      <Stack spacing={2.5}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="baseline"
        >
          <Box>
            <Typography variant="subtitle1" component="h2">
              {run.status === "pending" ? "Queued" : `${meta.label}…`}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {counted && total
                ? `${meta.detail}: ${formatCount(done)} of ${formatCount(total)} ${plural}`
                : meta.detail}
            </Typography>
          </Box>
          <Typography variant="caption" color="text.secondary">
            Running for {formatSpan(elapsed)}
          </Typography>
        </Stack>
        <StageRail current={current} />
        {counted && total > 0 && (
          <LinearProgress
            variant="determinate"
            value={Math.min(100, (done / total) * 100)}
            sx={{ height: 6, borderRadius: 3 }}
            aria-label={`${done} of ${total} ${plural}`}
          />
        )}
        {recent.length > 0 && (
          <Box>
            <Typography
              variant="overline"
              color="text.secondary"
              sx={{ display: "block", mb: 1 }}
            >
              Just read
            </Typography>
            <Stack
              spacing={1}
              aria-live="polite"
              sx={{ maxHeight: 260, overflow: "hidden" }}
            >
              {recent.slice(0, 6).map((text, index) => (
                <Typography
                  key={text}
                  variant="body2"
                  color={index === 0 ? "text.primary" : "text.secondary"}
                  sx={{
                    pl: 1.5,
                    borderLeft: "2px solid",
                    borderColor: index === 0 ? "primary.main" : "divider",
                    animation: `${arrive} 300ms ease-out`,
                    opacity: Math.max(0.45, 1 - index * 0.12),
                  }}
                >
                  {text}
                </Typography>
              ))}
            </Stack>
          </Box>
        )}
        <Typography variant="caption" color="text.secondary">
          Usually 2–10 minutes. You can leave this page; the report keeps
          running and will be here when you come back.
        </Typography>
      </Stack>
    </Paper>
  );
}

RunProgress.propTypes = { run: PropTypes.object.isRequired };
