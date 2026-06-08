import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";

const SAMPLE_NOTICE =
  "Sample data is ready for preview. It does not finish setup; connect real data to complete the workflow.";

const VERDICT_COLOR = {
  improved: "success",
  regressed: "error",
  unchanged: "default",
};

const VERDICT_ICON = {
  improved: "mdi:trending-up",
  regressed: "mdi:trending-down",
  unchanged: "mdi:equal",
};

function SampleCard({ children, sx }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 1.25,
        ...sx,
      }}
    >
      {children}
    </Box>
  );
}

SampleCard.propTypes = {
  children: PropTypes.node,
  sx: PropTypes.object,
};

function PromptDiffBody({ fixture }) {
  return (
    <Stack spacing={1.25}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          gap: 1,
        }}
      >
        {fixture.versions.map((version) => (
          <SampleCard key={version.id}>
            <Typography variant="subtitle2">{version.label}</Typography>
            <Typography variant="caption" color="text.secondary">
              {version.note}
            </Typography>
          </SampleCard>
        ))}
      </Box>
      <Stack spacing={1}>
        {fixture.cases.map((sampleCase) => (
          <SampleCard
            key={sampleCase.id}
            sx={{ bgcolor: "background.neutral" }}
          >
            <Stack spacing={0.75}>
              <Stack
                direction="row"
                spacing={0.75}
                alignItems="center"
                justifyContent="space-between"
                flexWrap="wrap"
                useFlexGap
              >
                <Typography variant="subtitle2">{sampleCase.title}</Typography>
                <Chip
                  size="small"
                  color={VERDICT_COLOR[sampleCase.verdict] || "default"}
                  variant={
                    sampleCase.verdict === "unchanged" ? "outlined" : "filled"
                  }
                  icon={
                    <Iconify
                      icon={VERDICT_ICON[sampleCase.verdict] || "mdi:equal"}
                      width={14}
                    />
                  }
                  label={sampleCase.verdictLabel}
                />
              </Stack>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                  gap: 1,
                }}
              >
                <Box>
                  <Typography variant="caption" color="text.disabled">
                    Baseline (v1)
                  </Typography>
                  <Typography variant="body2">{sampleCase.v1}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.disabled">
                    Edit (v2)
                  </Typography>
                  <Typography variant="body2">{sampleCase.v2}</Typography>
                </Box>
              </Box>
              <Typography variant="caption" color="text.secondary">
                {sampleCase.reason}
              </Typography>
            </Stack>
          </SampleCard>
        ))}
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
          gap: 1,
        }}
      >
        {fixture.metrics.map((metric) => (
          <SampleCard key={metric.label}>
            <Typography variant="caption" color="text.secondary">
              {metric.label}
            </Typography>
            <Typography variant="subtitle2">
              {metric.from} {"→"} {metric.to}
            </Typography>
          </SampleCard>
        ))}
      </Box>
    </Stack>
  );
}

PromptDiffBody.propTypes = { fixture: PropTypes.object.isRequired };

function EvalRunBody({ fixture }) {
  const { distribution } = fixture;
  const passPct = Math.round((distribution.pass / distribution.total) * 100);
  return (
    <Stack spacing={1.25}>
      <SampleCard>
        <Stack spacing={0.75}>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            justifyContent="space-between"
            flexWrap="wrap"
            useFlexGap
          >
            <Typography variant="subtitle2">
              {distribution.pass} pass / {distribution.fail} fail
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {distribution.total} examples - {passPct}% passing
            </Typography>
          </Stack>
          <Box
            sx={{
              display: "flex",
              height: 8,
              borderRadius: 4,
              overflow: "hidden",
              bgcolor: "background.neutral",
            }}
          >
            <Box
              sx={{ width: `${passPct}%`, bgcolor: "success.main" }}
              aria-hidden
            />
            <Box
              sx={{ width: `${100 - passPct}%`, bgcolor: "error.main" }}
              aria-hidden
            />
          </Box>
          <Typography variant="caption" color="text.secondary">
            {fixture.passSummary}
          </Typography>
        </Stack>
      </SampleCard>
      <Stack spacing={1}>
        {fixture.failureGroups.map((group) => (
          <SampleCard key={group.cause} sx={{ bgcolor: "background.neutral" }}>
            <Stack spacing={0.75}>
              <Stack
                direction="row"
                spacing={0.75}
                alignItems="center"
                flexWrap="wrap"
                useFlexGap
              >
                <Chip
                  size="small"
                  color="error"
                  variant="outlined"
                  icon={<Iconify icon="mdi:alert-circle-outline" width={14} />}
                  label={`${group.cause} (${group.count})`}
                />
              </Stack>
              {group.rows.map((row) => (
                <Box key={row.id}>
                  <Typography variant="caption" color="text.disabled">
                    {row.id}
                  </Typography>
                  <Typography variant="body2">{row.input}</Typography>
                  <Typography variant="caption" color="success.main">
                    Expected: {row.expected}
                  </Typography>
                  <Typography
                    variant="caption"
                    color="error.main"
                    display="block"
                  >
                    Got: {row.got}
                  </Typography>
                </Box>
              ))}
            </Stack>
          </SampleCard>
        ))}
      </Stack>
    </Stack>
  );
}

EvalRunBody.propTypes = { fixture: PropTypes.object.isRequired };

const STEP_STATUS = {
  ok: { color: "text.secondary", icon: "mdi:check-circle-outline" },
  warning: { color: "warning.main", icon: "mdi:alert-outline" },
  failed: { color: "error.main", icon: "mdi:close-circle-outline" },
};

function AgentTraceBody({ fixture }) {
  return (
    <Stack spacing={1.25}>
      <SampleCard>
        <Typography variant="caption" color="text.disabled">
          Scenario
        </Typography>
        <Typography variant="body2">{fixture.scenario}</Typography>
      </SampleCard>
      <Stack spacing={0.75}>
        {fixture.steps.map((step, index) => {
          const status = STEP_STATUS[step.status] || STEP_STATUS.ok;
          return (
            <SampleCard
              key={step.id}
              sx={{
                bgcolor:
                  step.status === "failed"
                    ? "error.lighter"
                    : "background.neutral",
              }}
            >
              <Stack direction="row" spacing={1} alignItems="flex-start">
                <Iconify
                  icon={status.icon}
                  width={18}
                  sx={{ color: status.color, mt: 0.25, flexShrink: 0 }}
                />
                <Box sx={{ minWidth: 0 }}>
                  <Stack
                    direction="row"
                    spacing={0.75}
                    alignItems="center"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <Typography variant="subtitle2">
                      {index + 1}. {step.label}
                    </Typography>
                    <Chip
                      size="small"
                      variant="outlined"
                      label={step.kind === "tool" ? "Tool call" : "Reasoning"}
                    />
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {step.detail}
                  </Typography>
                </Box>
              </Stack>
            </SampleCard>
          );
        })}
      </Stack>
      <SampleCard sx={{ borderColor: "error.main" }}>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Iconify
            icon="mdi:target"
            width={18}
            sx={{ color: "error.main", mt: 0.25, flexShrink: 0 }}
          />
          <Box>
            <Typography variant="subtitle2">
              Failure cause: {fixture.failureCause.step}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {fixture.failureCause.detail}
            </Typography>
          </Box>
        </Stack>
      </SampleCard>
    </Stack>
  );
}

AgentTraceBody.propTypes = { fixture: PropTypes.object.isRequired };

function VoiceCallBody({ fixture }) {
  return (
    <Stack spacing={1.25}>
      <SampleCard sx={{ bgcolor: "background.neutral" }}>
        <Stack spacing={0.75}>
          {fixture.transcript.map((turn) => (
            <Box key={turn.id}>
              <Stack
                direction="row"
                spacing={0.75}
                alignItems="center"
                flexWrap="wrap"
                useFlexGap
              >
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ fontVariantNumeric: "tabular-nums", minWidth: 36 }}
                >
                  {turn.at}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{ textTransform: "capitalize", fontWeight: 600 }}
                >
                  {turn.speaker}
                </Typography>
                {turn.interrupted ? (
                  <Chip
                    size="small"
                    color="warning"
                    variant="outlined"
                    label="Interrupted"
                  />
                ) : null}
              </Stack>
              <Typography variant="body2">{turn.text}</Typography>
              {turn.note ? (
                <Typography variant="caption" color="warning.main">
                  {turn.note}
                </Typography>
              ) : null}
            </Box>
          ))}
        </Stack>
      </SampleCard>
      <Typography variant="caption" color="text.secondary">
        {fixture.timingNote}
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
          gap: 1,
        }}
      >
        {fixture.extracted.map((field) => (
          <SampleCard key={field.label}>
            <Typography variant="caption" color="text.secondary">
              {field.label}
            </Typography>
            <Typography variant="subtitle2">{field.value}</Typography>
          </SampleCard>
        ))}
      </Box>
      <Box
        sx={{
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 1,
          p: 1,
        }}
      >
        <Typography variant="caption" color="text.secondary">
          {fixture.laterStepNote}
        </Typography>
      </Box>
    </Stack>
  );
}

VoiceCallBody.propTypes = { fixture: PropTypes.object.isRequired };

function GatewayLogBody({ fixture }) {
  const { logRow, snippet } = fixture;
  const rows = [
    { label: "Requested", value: logRow.requested },
    { label: "Served", value: logRow.served },
    { label: "Status", value: logRow.status },
    { label: "Latency", value: logRow.latency },
    { label: "Cost", value: logRow.cost },
  ];
  return (
    <Stack spacing={1.25}>
      <SampleCard sx={{ bgcolor: "background.neutral" }}>
        <Stack spacing={0.75}>
          <Stack
            direction="row"
            spacing={0.75}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
          >
            <Typography variant="caption" color="text.disabled">
              {logRow.requestId}
            </Typography>
            <Chip
              size="small"
              color="warning"
              variant="outlined"
              icon={<Iconify icon="mdi:swap-horizontal" width={14} />}
              label="Provider fallback"
            />
          </Stack>
          <Typography variant="body2" color="warning.main">
            {logRow.fallback}
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(5, 1fr)" },
              gap: 0.75,
            }}
          >
            {rows.map((row) => (
              <Box key={row.label}>
                <Typography variant="caption" color="text.secondary">
                  {row.label}
                </Typography>
                <Typography variant="body2">{row.value}</Typography>
              </Box>
            ))}
          </Box>
        </Stack>
      </SampleCard>
      <SampleCard>
        <Stack spacing={0.5}>
          <Typography variant="subtitle2">{snippet.label}</Typography>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1,
              borderRadius: 1,
              bgcolor: "background.neutral",
              fontFamily: "monospace",
              fontSize: 12,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            <Box component="span" sx={{ color: "text.disabled" }}>
              - {snippet.before}
            </Box>
            {"\n"}
            <Box component="span" sx={{ color: "success.main" }}>
              + {snippet.after}
            </Box>
          </Box>
        </Stack>
      </SampleCard>
    </Stack>
  );
}

GatewayLogBody.propTypes = { fixture: PropTypes.object.isRequired };

const LAYOUT_BODIES = {
  promptDiff: PromptDiffBody,
  evalRun: EvalRunBody,
  agentTrace: AgentTraceBody,
  voiceCall: VoiceCallBody,
  gatewayLog: GatewayLogBody,
};

function SampleStarterRunner({ Body, fixture }) {
  const starterAction = fixture.starterAction;
  const [sampleRan, setSampleRan] = useState(!starterAction);

  if (!starterAction) return <Body fixture={fixture} />;

  return (
    <Stack spacing={1.25}>
      <SampleCard>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          alignItems={{ xs: "stretch", sm: "center" }}
          justifyContent="space-between"
        >
          <Stack spacing={0.5} sx={{ minWidth: 0 }}>
            <Stack
              direction="row"
              spacing={0.75}
              alignItems="center"
              flexWrap="wrap"
              useFlexGap
            >
              <Chip size="small" variant="outlined" label="Starter content" />
              {sampleRan ? (
                <Chip
                  size="small"
                  color="success"
                  icon={<Iconify icon="mdi:check" width={14} />}
                  label={starterAction.resultLabel}
                />
              ) : null}
            </Stack>
            <Typography variant="body2" color="text.secondary">
              {starterAction.description}
            </Typography>
            <Typography variant="caption" color="text.disabled">
              Runs locally in this browser. It does not complete setup.
            </Typography>
          </Stack>
          <Button
            type="button"
            variant={sampleRan ? "outlined" : "contained"}
            color={sampleRan ? "success" : "primary"}
            onClick={() => setSampleRan(true)}
            startIcon={
              <Iconify
                icon={sampleRan ? "mdi:replay" : "mdi:play-circle-outline"}
                width={18}
              />
            }
            sx={{
              flexShrink: 0,
              alignSelf: { xs: "stretch", sm: "center" },
            }}
          >
            {sampleRan ? "Run again" : starterAction.label}
          </Button>
        </Stack>
      </SampleCard>

      {sampleRan ? (
        <Box data-testid="path-sample-preview-result" aria-live="polite">
          <Body fixture={fixture} />
        </Box>
      ) : (
        <SampleCard
          sx={{
            bgcolor: "background.paper",
            borderStyle: "dashed",
          }}
        >
          <Stack direction="row" spacing={1} alignItems="center">
            <Iconify
              icon="mdi:cursor-default-click-outline"
              width={18}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography variant="body2" color="text.secondary">
              Run the starter sample above to reveal the sample result here.
            </Typography>
          </Stack>
        </SampleCard>
      )}
    </Stack>
  );
}

SampleStarterRunner.propTypes = {
  Body: PropTypes.elementType.isRequired,
  fixture: PropTypes.object.isRequired,
};

export default function PathSamplePreviewPanel({
  fixture,
  realSetupHref,
  primaryPath,
}) {
  const [hidden, setHidden] = useState(false);

  if (!fixture) return null;
  if (hidden) return null;

  const Body = LAYOUT_BODIES[fixture.layout];
  if (!Body) return null;

  return (
    <Box
      data-testid={`path-sample-preview-panel-${primaryPath || fixture.primaryPath}`}
      data-sample-preview="true"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={1.5}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          alignItems={{ xs: "flex-start", sm: "center" }}
          justifyContent="space-between"
        >
          <Stack
            direction="row"
            spacing={0.75}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
          >
            <Chip size="small" label="Sample" />
            <Chip size="small" variant="outlined" label="Preview only" />
            <Chip size="small" variant="outlined" label={fixture.eyebrow} />
          </Stack>
        </Stack>

        <Stack spacing={0.5}>
          <Typography variant="h6">{fixture.headline}</Typography>
          <Typography variant="body2" color="text.secondary">
            {fixture.summary}
          </Typography>
        </Stack>

        <Box
          data-testid="path-sample-preview-body"
          sx={{
            border: "1px dashed",
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "background.neutral",
            p: 1.5,
          }}
        >
          <SampleStarterRunner Body={Body} fixture={fixture} />
        </Box>

        <Stack
          direction="row"
          spacing={0.75}
          alignItems="flex-start"
          sx={{ color: "text.secondary" }}
        >
          <Iconify
            icon="mdi:lightbulb-on-outline"
            width={16}
            sx={{ mt: 0.25, flexShrink: 0 }}
          />
          <Typography variant="body2" color="text.secondary">
            {fixture.takeaway}
          </Typography>
        </Stack>

        <Typography variant="caption" color="text.secondary">
          {SAMPLE_NOTICE}
        </Typography>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button
            variant="contained"
            component={RouterLink}
            href={realSetupHref}
            disabled={!realSetupHref}
            startIcon={<Iconify icon="mdi:connection" width={18} />}
            sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
          >
            {fixture.ctaLabel}
          </Button>
          <Button
            variant="text"
            color="inherit"
            onClick={() => setHidden(true)}
            startIcon={<Iconify icon="mdi:close" width={18} />}
            sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
          >
            Hide sample
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}

PathSamplePreviewPanel.propTypes = {
  fixture: PropTypes.object,
  primaryPath: PropTypes.string,
  realSetupHref: PropTypes.string,
};
