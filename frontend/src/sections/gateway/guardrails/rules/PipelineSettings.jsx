import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";

import { GatewayStatusChip } from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { nodePipelineSentence } from "../../utils/guardrailPipeline";
import RequestRoutesCard from "../RequestRoutesCard";

const DEFAULT_TIMEOUT_MS = 5000;

/**
 * The section-wide settings. The gateway applies the org's check timeout
 * (guardrails/plugin.go reads Guardrails.TimeoutMs), and the org's own
 * fail-open and parallel/sequential mode. This page shows the organization's
 * stored Mode and Fail Open read-only, with a link to Settings, where they
 * are changed; with no stored section it shows the gateway's report
 * (`nodePipeline`, GET config guardrail_pipeline) as the gateway's default.
 * Which requests the checks on the request read is listed route by route
 * (RequestRoutesCard).
 */
function nodeChip(node) {
  if (!node || typeof node !== "object") return "Not reported";
  const mode =
    node.mode === "sequential"
      ? "Sequential"
      : node.mode === "parallel"
        ? "Parallel"
        : "Not reported";
  return `${mode} · Fail open ${node.fail_open ? "on" : "off"}`;
}

// The organization's stored Mode and Fail Open, read as the gateway reads
// them: a stored mode other than sequential runs in parallel, and a stored
// Fail Open is on only when it says on. A setting the organization never
// stored is null: the gateway's own setting applies to it. Null without a
// stored section.
function storedPipeline(guardrails) {
  if (!guardrails || typeof guardrails !== "object") return null;
  const mode = guardrails.pipeline_mode ?? guardrails.pipelineMode;
  const failOpen = guardrails.fail_open ?? guardrails.failOpen;
  return {
    mode: !mode ? null : mode === "sequential" ? "sequential" : "parallel",
    fail_open:
      failOpen === undefined || failOpen === null
        ? null
        : [true, "true"].includes(failOpen),
  };
}

// A Fail Open the organization never set shows the gateway's position: the
// one it reports, or on (the gateway's default) when it reports none.
function storedChip(stored, node) {
  const mode = !stored.mode
    ? "Mode not set"
    : stored.mode === "sequential"
      ? "Sequential"
      : "Parallel";
  if (stored.fail_open !== null) {
    return `${mode} · Fail open ${stored.fail_open ? "on" : "off"}`;
  }
  const gatewayOn =
    typeof node?.fail_open === "boolean" ? node.fail_open : true;
  return `${mode} · Fail open ${gatewayOn ? "on" : "off"} (gateway default)`;
}

const SETTINGS_HREF = `${paths.dashboard.gateway.settings}?tab=org-config`;

// The organization's Mode and Fail Open, read-only here (data-read-only):
// they are changed in Settings. Without a stored section the chip shows the
// gateway's report as its default. The sentence is always the gateway's
// default, which applies only to the checks in its own configuration.
function NodePipelineRow({ node, stored = null }) {
  return (
    <Box
      role="group"
      aria-label="Fail-open and run mode"
      data-read-only=""
      sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: { xs: "flex-start", sm: "center" },
          flexDirection: { xs: "column", sm: "row" },
          gap: 1,
        }}
      >
        <Typography typography="s2" color="text.primary">
          Mode and Fail Open
        </Typography>
        <GatewayStatusChip
          tone="neutral"
          label={
            stored
              ? storedChip(stored, node)
              : `Gateway default: ${nodeChip(node)}`
          }
        />
        <Link component={RouterLink} href={SETTINGS_HREF} typography="s2">
          Change in Settings
        </Link>
      </Box>
      <Typography typography="s3" color="text.secondary">
        {nodePipelineSentence(node)}
      </Typography>
    </Box>
  );
}

NodePipelineRow.propTypes = {
  node: PropTypes.object,
  stored: PropTypes.shape({
    mode: PropTypes.string,
    fail_open: PropTypes.bool,
  }),
};

const SECTION_SX = {
  display: "flex",
  flexDirection: "column",
  gap: 1.5,
  p: 2,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  backgroundColor: "background.paper",
};

/**
 * How this gateway runs checks, for an organization with none: the
 * gateway's reported Mode and Fail Open as its default, with its sentence,
 * and no timeout (there is no check to time out).
 */
export function NodePipelineNote({ nodePipeline }) {
  return (
    <Box component="section" aria-label="Pipeline" sx={SECTION_SX}>
      <Typography typography="s1" fontWeight="fontWeightMedium">
        Pipeline
      </Typography>
      <NodePipelineRow node={nodePipeline} />
    </Box>
  );
}

NodePipelineNote.propTypes = {
  // GET config guardrail_pipeline, as for PipelineSettings.
  nodePipeline: PropTypes.object,
};

export default function PipelineSettings({
  guardrails,
  onSaveTimeout,
  onTimeoutChange,
  busy,
  nodePipeline,
  canEdit = true,
}) {
  const stored = guardrails?.timeout_ms ?? guardrails?.timeoutMs;
  const [timeout, setTimeoutValue] = useState(
    String(stored ?? DEFAULT_TIMEOUT_MS),
  );

  useEffect(() => {
    setTimeoutValue(String(stored ?? DEFAULT_TIMEOUT_MS));
  }, [stored]);

  const parsed = Number(timeout);
  const valid = Number.isInteger(parsed) && parsed >= 100 && parsed <= 60000;
  const changed = valid && parsed !== Number(stored ?? DEFAULT_TIMEOUT_MS);

  return (
    <Box component="section" aria-label="Pipeline" sx={SECTION_SX}>
      <Box>
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Pipeline
        </Typography>
        <Typography typography="s2" color="text.secondary">
          Applies to every check above.
        </Typography>
      </Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        <TextField
          size="small"
          type="number"
          label="Check timeout (ms)"
          value={timeout}
          onChange={(event) => {
            const next = event.target.value;
            setTimeoutValue(next);
            // In a draft (the org-config editor) a valid value goes straight
            // into the draft; the editor's own Save stores it.
            const n = Number(next);
            if (
              onTimeoutChange &&
              Number.isInteger(n) &&
              n >= 100 &&
              n <= 60000
            ) {
              onTimeoutChange(n);
            }
          }}
          error={!valid}
          disabled={!canEdit}
          helperText={
            valid
              ? "A check that runs longer than this times out."
              : "Between 100 and 60000 ms."
          }
          inputProps={{ min: 100, max: 60000, step: 100 }}
          sx={{ width: { xs: "100%", sm: 240 } }}
        />
        {onSaveTimeout && (
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              size="small"
              variant="outlined"
              disabled={!changed || busy || !canEdit}
              onClick={() => onSaveTimeout(parsed)}
              sx={{ mt: 0.5 }}
            >
              Save timeout
            </Button>
          </GatewayAdminOnly>
        )}
      </Box>
      <NodePipelineRow
        node={nodePipeline}
        stored={storedPipeline(guardrails)}
      />
      <RequestRoutesCard />
    </Box>
  );
}

PipelineSettings.propTypes = {
  guardrails: PropTypes.object,
  onSaveTimeout: PropTypes.func,
  onTimeoutChange: PropTypes.func,
  busy: PropTypes.bool,
  // GET config guardrail_pipeline: {enabled, fail_open, mode}, how this
  // gateway runs an organization's checks; absent when it does not say.
  nodePipeline: PropTypes.object,
  // Owner or Admin (useGatewayAdmin): only they may change the timeout.
  canEdit: PropTypes.bool,
};
