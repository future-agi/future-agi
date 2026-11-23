import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import StatusBadge from "src/components/status-badge/StatusBadge";

import { LIFECYCLE_COPY } from "./scenarios.constants";
import { lifecycleChip, wrongWayRows } from "./lifecycleView";
import ValidateScenariosButton from "./ValidateScenariosButton";

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
  if (chip) return chip.hover ? `${chip.label}. ${chip.hover}` : chip.label;
  return lifecycle.state === "validated"
    ? LIFECYCLE_COPY.validatedLabel
    : LIFECYCLE_COPY.activeLabel;
};

// The Status sheet: why the scenario is in its state, whether it is proved, and each wrong-way example.
export function LifecycleSection({ lifecycle, Row, sectionSx }) {
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
  lifecycle: PropTypes.object,
  Row: PropTypes.elementType.isRequired,
  sectionSx: PropTypes.object,
};
