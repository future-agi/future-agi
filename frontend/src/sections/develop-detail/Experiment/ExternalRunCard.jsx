import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { fToNowStrict } from "src/utils/format-time";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import RunCardShell from "./RunCardShell";
import { useExperimentForm } from "./ExperimentFormContext";
import { PROMPT_CONFIG_TYPE } from "./constants";

/**
 * L7b G2 (legacy:r2-run-cards): a kept run's name is what it ran as, shown
 * as text (an existing run is never renamed into a "Re-run"); a new card
 * takes a name.
 */
const RunNameField = ({
  control,
  fieldPrefix,
  kept,
  placeholder,
  required,
}) => {
  const runName = useWatch({ control, name: `${fieldPrefix}.runName` });
  if (kept)
    return (
      <Box sx={{ pt: 0.25 }}>
        <Typography typography="s3" color="text.secondary">
          Run name
        </Typography>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          data-testid="kept-run-name"
        >
          {runName || "—"}
        </Typography>
      </Box>
    );
  return (
    <FormTextFieldV2
      control={control}
      fieldName={`${fieldPrefix}.runName`}
      label="Run name"
      placeholder={placeholder}
      size="small"
      fullWidth
      required={required}
      sx={{ bgcolor: "background.paper" }}
    />
  );
};

RunNameField.propTypes = {
  control: PropTypes.object.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  kept: PropTypes.bool,
  placeholder: PropTypes.string,
  required: PropTypes.bool,
};

/**
 * From code (SDK / CI) run, kept when an experiment is edited: your code
 * ran each example and uploaded one result per example and trial. dataset integration
 * FE-0: no upload snippet or generated script (a copy never carries this
 * card; FE-1 prints the real SDK command).
 */
const FromCodeCard = ({
  control,
  fieldPrefix,
  letters = [],
  onRemove,
  showErrors = false,
  error,
}) => {
  const { runsById } = useExperimentForm();
  const runId = useWatch({ control, name: `${fieldPrefix}.runId` });
  const readOnly = useWatch({ control, name: `${fieldPrefix}.readOnly` });
  const run = runId ? runsById?.[runId] : null;
  const status = run ? getRunStatus(run) : null;
  return (
    <RunCardShell
      icon={
        <Iconify
          icon="mdi:code-braces"
          width={16}
          sx={{ color: "text.secondary" }}
        />
      }
      letters={letters}
      title={readOnly ? run?.name ?? "Kept run" : "From code"}
      typeLabel={readOnly ? "Kept as it ran" : "SDK / CI"}
      onRemove={readOnly ? undefined : onRemove}
      error={showErrors ? error : null}
    >
      <Box sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
        <Box sx={{ width: 260, flexShrink: 0 }}>
          <RunNameField
            control={control}
            fieldPrefix={fieldPrefix}
            kept={Boolean(runId)}
            placeholder="my-agent@main"
            required
          />
        </Box>
        <Typography typography="s2" color="text.secondary" sx={{ pt: 0.5 }}>
          {readOnly
            ? "This run is kept with its results; it cannot be changed here."
            : "Your code ran each example and uploaded the output; evaluations scored each upload here."}
        </Typography>
      </Box>
      {run ? (
        <Typography typography="s2" color="text.secondary">
          {run.label} · {status.label} · {run.progress?.done ?? 0}/
          {run.progress?.total ?? 0}
          {run.created_at ? ` · created ${fToNowStrict(run.created_at)}` : ""}
        </Typography>
      ) : null}
    </RunCardShell>
  );
};

FromCodeCard.propTypes = {
  control: PropTypes.object.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  letters: PropTypes.arrayOf(PropTypes.string),
  onRemove: PropTypes.func,
  showErrors: PropTypes.bool,
  error: PropTypes.string,
  trials: PropTypes.number,
};

/**
 * My code run (R2-SPEC §5.3.3), kept when an experiment is edited: your
 * code ran each row on your side and uploaded the results. dataset integration FE-0:
 * no mode toggle, generated runner text or traces switch (a copy never
 * carries this card; FE-1 prints the real SDK command).
 */
const MyCodeCard = ({
  control,
  fieldPrefix,
  letters = [],
  onRemove,
  showErrors = false,
  error,
}) => {
  const { runsById } = useExperimentForm();
  const runId = useWatch({ control, name: `${fieldPrefix}.runId` });
  const run = runId ? runsById?.[runId] : null;
  const status = run ? getRunStatus(run) : null;
  return (
    <RunCardShell
      icon={
        <Iconify
          icon="mdi:code-braces"
          width={16}
          sx={{ color: "text.secondary" }}
        />
      }
      letters={letters}
      title="My code"
      // options-truth L1 C11: no Preview word on the SDK mode.
      onRemove={onRemove}
      error={showErrors ? error : null}
    >
      <Box sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
        <Box sx={{ width: 260, flexShrink: 0 }}>
          <RunNameField
            control={control}
            fieldPrefix={fieldPrefix}
            kept={Boolean(runId)}
            placeholder="LangGraph support agent"
          />
        </Box>
        <Typography typography="s2" color="text.secondary" sx={{ pt: 0.5 }}>
          Your code ran each row on your side and uploaded the results with
          their trace ids.
        </Typography>
      </Box>
      {run ? (
        <Typography typography="s2" color="text.secondary">
          {run.label} · {status.label} · {run.progress?.done ?? 0}/
          {run.progress?.total ?? 0}
          {run.created_at ? ` · created ${fToNowStrict(run.created_at)}` : ""}
        </Typography>
      ) : null}
    </RunCardShell>
  );
};

MyCodeCard.propTypes = {
  control: PropTypes.object.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  letters: PropTypes.arrayOf(PropTypes.string),
  onRemove: PropTypes.func,
  showErrors: PropTypes.bool,
  error: PropTypes.string,
};

/**
 * From code (SDK / CI) upload runs (parity, unchanged) and R2 `My code`
 * runs share this slot of ConfigureStepLLM; the card's targetType picks.
 */
const ExternalRunCard = (props) => {
  const type = useWatch({
    control: props.control,
    name: `${props.fieldPrefix}.targetType`,
  });
  return type === PROMPT_CONFIG_TYPE.CODE ? (
    <MyCodeCard {...props} />
  ) : (
    <FromCodeCard {...props} />
  );
};

ExternalRunCard.propTypes = {
  control: PropTypes.object.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  letters: PropTypes.arrayOf(PropTypes.string),
  onRemove: PropTypes.func,
  showErrors: PropTypes.bool,
  error: PropTypes.string,
  trials: PropTypes.number,
};

/** Stepper issues of a My code card (stepIssues.js): none to fix. */
export const issues = () => [];

export default ExternalRunCard;
