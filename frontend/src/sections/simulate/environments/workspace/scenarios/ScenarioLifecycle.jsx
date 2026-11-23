import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";

import StatusBadge from "src/components/status-badge/StatusBadge";

import { LIFECYCLE_COPY } from "./scenarios.constants";
import { citationState, lifecycleChip, wrongWayRows } from "./lifecycleView";

const CITATION_ROLES = {
  confirmed: "success",
  changed: "warning",
  unconfirmed: "danger",
};

function CitationBadge({ state }) {
  if (!state) return null;
  return (
    <StatusBadge
      status={state}
      role={CITATION_ROLES[state]}
      label={LIFECYCLE_COPY.citation[state]}
      size="sm"
      dot={false}
    />
  );
}
CitationBadge.propTypes = { state: PropTypes.string };
import ValidateScenariosButton from "./ValidateScenariosButton";
import SourceCompareDialog from "./SourceCompareDialog";
import { NotMeasuredChip } from "./ScenarioTableCells";

// A rule or file the scenario cites changed: say where, and open the before and after.
function SourceChangedLine({ envId, lifecycle }) {
  const [open, setOpen] = useState(false);
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography className="sd-val" sx={{ flex: 1, minWidth: 0 }}>
        {LIFECYCLE_COPY.sourceChanged.line(lifecycle.source_changed_version)}
      </Typography>
      {envId && lifecycle.source_changed_version_id && (
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => setOpen(true)}
          sx={{ flexShrink: 0 }}
        >
          {LIFECYCLE_COPY.sourceChanged.compare}
        </Button>
      )}
      <SourceCompareDialog
        open={open}
        onClose={() => setOpen(false)}
        envId={envId}
        versionId={lifecycle.source_changed_version_id}
        sources={lifecycle.sources ?? []}
      />
    </Stack>
  );
}
SourceChangedLine.propTypes = {
  envId: PropTypes.string,
  lifecycle: PropTypes.object,
};

const RESULT_ROLES = {
  caught: "success",
  not_caught: "danger",
  not_run: "neutral",
};

// The drawer's second header line: state, must-pass and Validate for this one scenario.
export function LifecycleHeaderLine({ envId, row, locked = false }) {
  const lifecycle = row?.lifecycle;
  if (!lifecycle) return null;
  const chip = lifecycleChip(lifecycle);
  // An unsaved change validates with the unsaved changes, not on its own.
  const canValidate =
    !locked && envId && row.scenarioKey && lifecycle.reason !== "in_draft";
  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      columnGap={1}
      rowGap={0.5}
    >
      {chip && (
        <StatusBadge
          status={chip.key}
          role={chip.tone}
          label={chip.label}
          size="sm"
          dot={false}
        />
      )}
      <NotMeasuredChip lifecycle={lifecycle} />
      {lifecycle.must_pass && (
        <StatusBadge
          status="must_pass"
          role="discovery"
          label={LIFECYCLE_COPY.mustPass}
          size="sm"
          dot={false}
        />
      )}
      {canValidate && (
        <ValidateScenariosButton
          envId={envId}
          keys={[row.scenarioKey]}
          label={LIFECYCLE_COPY.validate.one}
        />
      )}
    </Stack>
  );
}
LifecycleHeaderLine.propTypes = {
  envId: PropTypes.string,
  row: PropTypes.object,
  locked: PropTypes.bool,
};

const stateText = (lifecycle) => {
  const chip = lifecycleChip(lifecycle);
  if (chip) {
    // Kept as written still runs; say so before the reason, so it doesn't read as blocked.
    const runs =
      chip.key === "kept_as_written" ? ` ${LIFECYCLE_COPY.keptRuns}` : "";
    return `${chip.label}.${runs}${chip.hover ? ` ${chip.hover}` : ""}`;
  }
  return lifecycle.state === "validated"
    ? LIFECYCLE_COPY.validatedLabel
    : LIFECYCLE_COPY.activeLabel;
};

// The Status sheet: why the scenario is in its state, whether it is proved, and each wrong-way example.
export function LifecycleSection({ envId, lifecycle, Row, sectionSx }) {
  if (!lifecycle) return null;
  const examples = wrongWayRows(lifecycle);
  const proof = lifecycle.proof;
  return (
    <Box data-testid="lifecycle-section">
      <Typography sx={sectionSx}>{LIFECYCLE_COPY.statusTitle}</Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          "& > .sr + .sr": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        <Row label={LIFECYCLE_COPY.stateLabel}>
          <Typography className="sd-val">{stateText(lifecycle)}</Typography>
        </Row>
        {lifecycle.sources?.length > 0 && (
          <Row label={LIFECYCLE_COPY.sourcesLabel}>
            <Stack spacing={0.5}>
              {lifecycle.sources.map((one, index) => (
                <Box
                  key={`${one.kind}-${one.ref}-${index}`}
                  data-testid="citation"
                >
                  <Stack direction="row" alignItems="flex-start" spacing={1}>
                    <Typography
                      className="sd-val"
                      sx={{ flex: 1, minWidth: 0 }}
                    >
                      {one.label}
                    </Typography>
                    <CitationBadge state={citationState(one, lifecycle)} />
                  </Stack>
                  {one.problem && (
                    <Typography
                      sx={{ typography: "s3", color: "status.warning.fg" }}
                    >
                      {one.problem}
                    </Typography>
                  )}
                </Box>
              ))}
            </Stack>
          </Row>
        )}
        {lifecycle.source_changed?.length > 0 && (
          <Row label={LIFECYCLE_COPY.sourceChanged.label}>
            <SourceChangedLine envId={envId} lifecycle={lifecycle} />
          </Row>
        )}
        {proof && (
          <Row label={LIFECYCLE_COPY.proofLabel}>
            <Typography className="sd-val">
              {proof.proved ? LIFECYCLE_COPY.proved : LIFECYCLE_COPY.notProved}
            </Typography>
          </Row>
        )}
        <Row label={LIFECYCLE_COPY.wrongWay.title}>
          {examples.length ? (
            <Stack spacing={1}>
              {examples.map((one, index) => (
                <Stack
                  key={`${one.name}-${index}`}
                  direction="row"
                  alignItems="flex-start"
                  spacing={1}
                  data-testid="wrong-way-row"
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      className="sd-val"
                      sx={{ color: "text.primary" }}
                    >
                      {one.name}
                    </Typography>
                    {one.why && (
                      <Typography
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {one.why}
                      </Typography>
                    )}
                  </Box>
                  <StatusBadge
                    status={one.result}
                    role={RESULT_ROLES[one.result]}
                    label={LIFECYCLE_COPY.wrongWay[one.result]}
                    size="sm"
                    dot={false}
                  />
                </Stack>
              ))}
            </Stack>
          ) : (
            <Typography className="sd-val">
              {LIFECYCLE_COPY.wrongWay.none}
            </Typography>
          )}
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
          >
            {LIFECYCLE_COPY.wrongWay.hint}
          </Typography>
        </Row>
      </Box>
    </Box>
  );
}
LifecycleSection.propTypes = {
  envId: PropTypes.string,
  lifecycle: PropTypes.object,
  Row: PropTypes.elementType.isRequired,
  sectionSx: PropTypes.object,
};
