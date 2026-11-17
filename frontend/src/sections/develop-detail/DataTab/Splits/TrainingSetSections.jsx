// The sections of Export as training set (TrainingSetDialog.jsx): the
// pickers, the message roles, the check lines with their fixes, and the
// saved files. Every line's text comes from trainingSetModel.js.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Link,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import { fmtCount } from "src/sections/common/format/fmt";
import { saveFile } from "src/sections/common/export/datasetExport";
import { sizeText } from "src/sections/common/export/writers";
import {
  countsLine,
  emptyTrainLine,
  leakAction,
  leakLine,
  leakPairs,
  leftOutLine,
  noInputLine,
  overlapAction,
  overlapLines,
  trainingFileName,
} from "./trainingSetModel";

// Where a custom model is added (the Settings nav's AI Providers page).
const AI_PROVIDERS_PATH = "/dashboard/settings/ai-providers";
const labelSx = { typography: "s2", color: "text.secondary", mb: 0.5 };
const ROLE_FIELDS = [
  ["user", "User message", false],
  ["turns", "Earlier turns", true],
  ["assistant", "Assistant reply", false],
  ["toolCalls", "Tool calls", true],
];

/** A labelled select; `none` adds a None option. */
export function Pick({
  label,
  value,
  options,
  onChange,
  none,
  testId,
  placeholder = "Pick a split",
}) {
  return (
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Typography sx={labelSx}>{label}</Typography>
      <Select
        size="small"
        fullWidth
        displayEmpty
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        inputProps={{ "aria-label": label, "data-testid": testId }}
        sx={{ typography: "s2" }}
      >
        {none && <MenuItem value="">None</MenuItem>}
        {!none && !value && (
          <MenuItem value="" disabled>
            {placeholder}
          </MenuItem>
        )}
        {options.map((o) => (
          <MenuItem key={o.id} value={o.id} disabled={o.disabled}>
            {o.label}
          </MenuItem>
        ))}
      </Select>
    </Box>
  );
}

Pick.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  none: PropTypes.bool,
  testId: PropTypes.string,
  placeholder: PropTypes.string,
};

/** A check line: the finding and its fix, or Undo once applied. */
function Check({ text, detail, action, applied, onToggle, testId }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-testid={testId}
      data-applied={applied ? "true" : "false"}
      sx={{
        px: 1.25,
        py: 0.75,
        borderRadius: 1,
        border: "1px solid",
        borderColor: applied ? "divider" : "warning.main",
      }}
    >
      <Iconify
        icon={applied ? "mdi:check-circle-outline" : "mdi:alert-outline"}
        width={18}
        sx={{ color: applied ? "success.main" : "warning.main", flexShrink: 0 }}
      />
      <Typography typography="s2" title={detail} sx={{ flex: 1, minWidth: 0 }}>
        {text}
      </Typography>
      <Button
        size="small"
        onClick={onToggle}
        data-testid={`${testId}-action`}
        sx={{ flexShrink: 0 }}
      >
        {applied ? "Undo" : action}
      </Button>
    </Stack>
  );
}

Check.propTypes = {
  text: PropTypes.string.isRequired,
  detail: PropTypes.string,
  action: PropTypes.string.isRequired,
  applied: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  testId: PropTypes.string,
};

/**
 * Which columns become which messages (from the column roles, changeable),
 * and an optional system message on every line.
 */
export function MessagesSection({
  columns,
  roles,
  onRoles,
  system,
  onSystem,
  blocker,
}) {
  const [editing, setEditing] = useState(false);
  const nameOf = (id) => columns.find((c) => c.id === id)?.name ?? id;
  const summary = ROLE_FIELDS.filter(([k]) => roles[k])
    .map(([k, label]) => `${label.toLowerCase()}: ${nameOf(roles[k])}`)
    .concat(system.trim() ? ["system message"] : [])
    .join(" · ");
  return (
    <Box>
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography
          typography="s2"
          color={blocker ? "error.main" : "text.secondary"}
          sx={{ flex: 1, minWidth: 0 }}
          data-testid="training-set-roles"
        >
          {blocker || (summary ? `Messages · ${summary}` : "Messages")}
        </Typography>
        <Button
          size="small"
          onClick={() => setEditing((s) => !s)}
          disabled={!columns.length}
          data-testid="training-set-roles-edit"
        >
          {editing ? "Done" : "Change"}
        </Button>
      </Stack>
      {editing && (
        <Stack gap={1} sx={{ mt: 1 }}>
          {ROLE_FIELDS.map(([k, label, optional]) => (
            <Stack key={k} direction="row" alignItems="center" gap={1.5}>
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ width: 140, flexShrink: 0 }}
              >
                {label}
              </Typography>
              <Select
                size="small"
                displayEmpty
                value={roles[k] ?? ""}
                onChange={(e) =>
                  onRoles({ ...roles, [k]: e.target.value || null })
                }
                sx={{ flex: 1, typography: "s2" }}
                inputProps={{
                  "aria-label": label,
                  "data-testid": `training-set-role-${k}`,
                }}
              >
                {optional && <MenuItem value="">None</MenuItem>}
                {!optional && !roles[k] && (
                  <MenuItem value="" disabled>
                    Pick a column
                  </MenuItem>
                )}
                {columns.map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {`${c.name}${c.role ? ` · ${c.role}` : ""}`}
                  </MenuItem>
                ))}
              </Select>
            </Stack>
          ))}
          <TextField
            size="small"
            label="System message (optional)"
            value={system}
            onChange={(e) => onSystem(e.target.value)}
            multiline
            maxRows={4}
            inputProps={{ "data-testid": "training-set-system" }}
          />
        </Stack>
      )}
    </Box>
  );
}

MessagesSection.propTypes = {
  columns: PropTypes.array.isRequired,
  roles: PropTypes.object.isRequired,
  onRoles: PropTypes.func.isRequired,
  system: PropTypes.string.isRequired,
  onSystem: PropTypes.func.isRequired,
  blocker: PropTypes.string,
};

/** The counts, what is left out, the two checks and the file names. */
export function PlanSummary({
  plan,
  datasetName,
  versionNumber,
  onToggleOverlap,
  onToggleLeak,
}) {
  const lines = [
    ["training-set-left-out", leftOutLine(plan)],
    ["training-set-no-input", noInputLine(plan)],
  ].filter(([, t]) => t);
  const overlap = overlapLines(plan);
  const leaks = ["validation", "test"]
    .map((k) => [k, leakLine(plan, k)])
    .filter(([, t]) => t);
  const empty = emptyTrainLine(plan, versionNumber);
  return (
    <Stack gap={1}>
      <Typography
        typography="s1"
        fontWeight="fontWeightSemiBold"
        data-testid="training-set-counts"
      >
        {countsLine(plan)}
      </Typography>
      {lines.map(([id, t]) => (
        <Typography
          key={id}
          typography="s2"
          color="text.secondary"
          data-testid={id}
        >
          {t}
        </Typography>
      ))}
      {overlap.length > 0 && (
        <Check
          text={overlap.join(" · ")}
          action={overlapAction(plan)}
          applied={plan.keepOverlapInFirst}
          onToggle={onToggleOverlap}
          testId="training-set-overlap"
        />
      )}
      {leaks.map(([k, t]) => (
        <Check
          key={k}
          text={t}
          detail={leakPairs(plan, k)}
          action={leakAction(plan, k)}
          applied={plan.parts.find((p) => p.key === k)?.leakedOut}
          onToggle={() => onToggleLeak(k)}
          testId={`training-set-leak-${k}`}
        />
      ))}
      {empty && (
        <Typography
          typography="s2"
          color="warning.dark"
          data-testid="training-set-train-empty"
        >
          {empty}
        </Typography>
      )}
      {!empty && !overlap.length && !leaks.length && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="training-set-clean"
        >
          No row is in two files, and no validation or test input is already in
          train.
        </Typography>
      )}
      <Stack gap={0.25} sx={{ mt: 0.5 }} data-testid="training-set-files">
        {plan.parts
          .filter((p) => p.written > 0)
          .map((p) => (
            <Typography
              key={p.key}
              typography="s3"
              color="text.secondary"
              sx={{ fontFamily: "monospace" }}
            >
              {`${trainingFileName({ datasetName, versionNumber, key: p.key })} · split ${p.splitName} · ${fmtCount(p.written, "row")}`}
            </Typography>
          ))}
      </Stack>
    </Stack>
  );
}

PlanSummary.propTypes = {
  plan: PropTypes.object.isRequired,
  datasetName: PropTypes.string.isRequired,
  versionNumber: PropTypes.number,
  onToggleOverlap: PropTypes.func.isRequired,
  onToggleLeak: PropTypes.func.isRequired,
};

/** Unsaved changes are not in the files: `1 edited · 15 split changes`, Save version…. */
export function UnsavedNotice({ summary, onSave }) {
  return (
    <Stack direction="row" alignItems="center" gap={1}>
      <Typography
        typography="s2"
        color="warning.dark"
        sx={{ flex: 1, minWidth: 0 }}
        data-testid="training-set-unsaved"
      >
        {`Unsaved changes${summary ? ` (${summary})` : ""} are not in these files.`}
      </Typography>
      <Button
        size="small"
        variant="outlined"
        onClick={onSave}
        data-testid="training-set-save-version"
        sx={{ flexShrink: 0 }}
      >
        Save version…
      </Button>
    </Stack>
  );
}

UnsavedNotice.propTypes = {
  summary: PropTypes.string,
  onSave: PropTypes.func.isRequired,
};

/** The done state: the saved files (each can be saved again) and what next. */
export function SavedFiles({ saved, testName, note, onLeave }) {
  return (
    <Stack gap={2} data-testid="training-set-done">
      <Typography typography="s1" fontWeight="fontWeightSemiBold">
        {`Saved ${fmtCount(saved.length, "file")}`}
      </Typography>
      <Stack gap={0.5}>
        {saved.map((f) => (
          <Stack
            key={f.key}
            direction="row"
            alignItems="center"
            gap={1}
            data-testid={`training-set-saved-${f.key}`}
          >
            <Iconify
              icon="mdi:file-document-outline"
              width={18}
              sx={{ color: "text.secondary" }}
            />
            <Typography
              typography="s2"
              noWrap
              title={f.name}
              sx={{ fontFamily: "monospace", flex: 1, minWidth: 0 }}
            >
              {f.name}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {`split ${f.splitName} · ${fmtCount(f.lines, "row")} · ${sizeText(f.size)}`}
            </Typography>
            <IconButton
              size="small"
              aria-label={`Save ${f.name} again`}
              onClick={() => saveFile(f)}
            >
              <Iconify icon="mdi:download" width={18} />
            </IconButton>
          </Stack>
        ))}
      </Stack>
      <Typography typography="s2" data-testid="training-set-next">
        Next: train with your provider, add the tuned model in{" "}
        <Link
          component={RouterLink}
          to={AI_PROVIDERS_PATH}
          onClick={onLeave}
          data-testid="training-set-providers"
        >
          Settings › AI Providers
        </Link>{" "}
        › Create custom model, then compare it with the base model on{" "}
        {testName ?? "rows it was not trained on"}.
      </Typography>
      {note && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="training-set-split-note"
        >
          {note}
        </Typography>
      )}
    </Stack>
  );
}

SavedFiles.propTypes = {
  saved: PropTypes.array.isRequired,
  testName: PropTypes.string,
  note: PropTypes.string,
  onLeave: PropTypes.func,
};
