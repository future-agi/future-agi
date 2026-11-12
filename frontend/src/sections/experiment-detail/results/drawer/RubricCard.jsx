import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import LabelInput from "src/sections/annotations/queues/annotate/label-input";
import { isLabelValueEmpty } from "src/sections/annotations/queues/utils/label-value";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import { outputText } from "../outputText";
import { runLabel, runShortName } from "src/sections/common/Runs/runLabel";

/** "Quality 2/5" from a run's existing human value for a label. */
function teamValueText(label, value) {
  if (value == null) return null;
  if (label.type === "star")
    return `${label.name} ${Number(value).toFixed(1).replace(/\.0$/, "")}/${label.settings?.no_of_stars ?? 5}`;
  if (label.type === "thumbs_up_down")
    return `${label.name} ${value >= 0.5 ? "Yes" : "No"}`;
  return `${label.name} ${value}`;
}

/**
 * Rubric card (§3.11 Review tab, one run in view or `Rubric` picked): the
 * labels of the run's rubric queue (default `Correct?`, `Quality`) through
 * the annotate workspace's label-input, Notes, `⌘↵` submit + next, `S` skip.
 * Saves through V2 `mode: "rubric"`. No number keys.
 */
export default function RubricCard({
  run,
  result,
  color,
  labels,
  labelsLoading,
  values,
  onValueChange,
  notes,
  onNotesChange,
  teamValues,
  saving,
  onSubmit,
  onSkip,
  mode,
  queueName,
  saved,
  notesFooter,
  entryRefFor,
  errorIds,
}) {
  const missing = labels.filter((l) => isLabelValueEmpty(l.type, values[l.id]));
  const team = labels
    .map((l) => teamValueText(l, teamValues?.[l.id]))
    .filter(Boolean);
  return (
    <Stack spacing={1.5}>
      <Stack direction="row" alignItems="baseline" gap={1}>
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
        >
          Rate this response
        </Typography>
        {queueName ? (
          <Typography typography="s3" color="text.secondary" noWrap>
            Saves to “{queueName}”
          </Typography>
        ) : null}
      </Stack>

      <Stack spacing={0.75}>
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Box
            sx={{
              width: 3,
              height: 14,
              borderRadius: 1,
              bgcolor: color,
              flexShrink: 0,
            }}
          />
          <Typography typography="s2" fontWeight="fontWeightSemiBold">
            {runLabel(run)}
          </Typography>
          <Typography typography="s3" color="text.secondary" noWrap>
            {runShortName(run)}
          </Typography>
        </Stack>
        <Box
          sx={{
            p: 1.25,
            borderRadius: 0.5,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
            maxHeight: 220,
            overflow: "auto",
          }}
        >
          {result?.status === "error" ? (
            <Typography typography="s2" color="error.main">
              {result.error || "This run errored on this row."}
            </Typography>
          ) : !result || result.status === "missing" ? (
            <Typography
              typography="s2"
              color="text.disabled"
              sx={{ fontStyle: "italic" }}
            >
              Not run on this row
            </Typography>
          ) : (
            <ValueViewer
              value={
                mode === "raw" ? result.output : outputText(result.output)
              }
              mode={mode}
              clampChars={900}
              emptyLabel="Empty output"
            />
          )}
        </Box>
      </Stack>

      {labelsLoading ? (
        <Stack spacing={1}>
          <Skeleton variant="rounded" height={72} />
          <Skeleton variant="rounded" height={72} />
        </Stack>
      ) : (
        <Stack spacing={1.25}>
          {labels.map((label) => (
            <LabelInput
              key={label.id}
              label={{
                name: label.name,
                type: label.type,
                settings: label.settings ?? {},
                description: label.description,
                required: true,
                allow_notes: false,
              }}
              value={values[label.id] ?? null}
              onChange={(val) => onValueChange(label.id, val)}
              hasError={Boolean(errorIds?.has(label.id))}
              entryRef={entryRefFor?.(label.id)}
            />
          ))}
        </Stack>
      )}

      {team.length ? (
        <Typography typography="s2" color="text.secondary">
          Reviewers so far: {team.join(" · ")}
        </Typography>
      ) : null}

      <TextField
        size="small"
        multiline
        minRows={2}
        label="Notes"
        placeholder="What makes this response good or bad?"
        value={notes}
        onChange={(e) => onNotesChange(e.target.value)}
        fullWidth
      />
      {notesFooter}

      <Stack direction="row" alignItems="center" gap={1}>
        {saving ? <CircularProgress size={12} /> : null}
        <Typography
          typography="s2"
          color={saved ? "success.main" : "text.secondary"}
          sx={{ flex: 1 }}
        >
          {saved
            ? "Saved"
            : missing.length
              ? `Rate ${missing.map((l) => l.name).join(" and ")} to submit`
              : "Ready to submit"}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onSkip}
          sx={{ gap: 0.75, borderColor: "divider" }}
        >
          Skip <KeyCap>S</KeyCap>
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={saving || missing.length > 0 || labels.length === 0}
          onClick={onSubmit}
          sx={{ gap: 0.75 }}
        >
          Submit & next <KeyCap>⌘↵</KeyCap>
        </Button>
      </Stack>
    </Stack>
  );
}

RubricCard.propTypes = {
  run: PropTypes.object.isRequired,
  result: PropTypes.object,
  color: PropTypes.string,
  labels: PropTypes.arrayOf(PropTypes.object).isRequired,
  labelsLoading: PropTypes.bool,
  values: PropTypes.object.isRequired,
  onValueChange: PropTypes.func.isRequired,
  notes: PropTypes.string,
  onNotesChange: PropTypes.func.isRequired,
  teamValues: PropTypes.object,
  saving: PropTypes.bool,
  onSubmit: PropTypes.func.isRequired,
  onSkip: PropTypes.func.isRequired,
  mode: PropTypes.string,
  queueName: PropTypes.string,
  saved: PropTypes.bool,
  notesFooter: PropTypes.node,
  // KH B1: each field's pending refusal, and the labels a refused submit names.
  entryRefFor: PropTypes.func,
  errorIds: PropTypes.instanceOf(Set),
};
