import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import PathInput from "src/sections/common/Values/PathInput";
import { conversationSummary } from "src/sections/develop-detail/DataTab/Versions/versionText";
import { FieldToColumnPicker } from "src/components/traceDetailDrawer/addToDataset/AddExistingDataset";
import { DraftChip } from "./ImportItemPreview";
import { clipText } from "./mappingModel";
import { FIELD_OPTIONS, INCLUDE_FLAGS, RECIPES } from "./shapes";

const MODE_ICON = {
  auto: { icon: "mdi:auto-fix", label: "Auto-mapped" },
  field: { icon: "mdi:form-dropdown", label: "Field" },
  path: { icon: "mdi:code-json", label: "Path" },
  empty: { icon: "mdi:cancel", label: "Leave empty" },
};
const PATH = "__path__";
const EMPTY = "__empty__";

const ModeIcon = ({ mode }) => {
  const m = MODE_ICON[mode] ?? MODE_ICON.auto;
  return (
    <CustomTooltip show title={m.label} size="small" arrow>
      <Box
        component="span"
        aria-label={m.label}
        sx={{
          display: "inline-flex",
          color: mode === "auto" ? "text.disabled" : "primary.main",
          flexShrink: 0,
        }}
      >
        <Iconify icon={m.icon} width={16} />
      </Box>
    </CustomTooltip>
  );
};
ModeIcon.propTypes = { mode: PropTypes.string };

/** The value of row 1, clipped to 80 chars with the full text in a tooltip. */
const PreviewValue = ({ value, unmapped }) => {
  // IT-R2 G5 P3: a conversation previews as `Conversation · 8 turns · …`.
  const conversation = conversationSummary(value);
  if (conversation)
    return (
      <Typography typography="s3" color="text.secondary">
        {conversation}
      </Typography>
    );
  const text = clipText(value, 80);
  const full = clipText(value, 2000);
  if (!text)
    return (
      <Typography
        typography="s3"
        color={unmapped ? "warning.dark" : "text.disabled"}
      >
        {unmapped ? "No value" : "empty"}
      </Typography>
    );
  return (
    <CustomTooltip
      show={full.length > text.length}
      title={full}
      size="small"
      arrow
    >
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{
          fontFamily: "monospace",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          wordBreak: "break-word",
        }}
      >
        {text}
      </Typography>
    </CustomTooltip>
  );
};
PreviewValue.propTypes = { value: PropTypes.any, unmapped: PropTypes.bool };

const MappingRow = ({
  row,
  active,
  onActive,
  onOverride,
  fieldOptions,
  sampleRoot,
}) => {
  const theme = useTheme();
  const o = row.override;
  const mode = o?.mode ?? "auto";
  const reset = (
    <CustomTooltip show title="Back to auto" size="small" arrow>
      <IconButton
        size="small"
        aria-label="Back to auto"
        onClick={() => onOverride(null)}
        sx={{ p: 0.25 }}
      >
        <Iconify icon="mdi:close" width={14} />
      </IconButton>
    </CustomTooltip>
  );
  return (
    <Box
      onFocusCapture={onActive}
      onClick={onActive}
      data-testid={`add-data-map-${row.target}`}
      sx={{
        display: "grid",
        gridTemplateColumns: "140px 1fr",
        gap: 1.5,
        alignItems: "start",
        px: 1,
        py: 0.75,
        borderRadius: 1,
        borderLeft: "2px solid",
        borderLeftColor: active ? "primary.main" : "transparent",
        bgcolor: row.unmapped
          ? alpha(theme.palette.warning.main, 0.08)
          : active
            ? "action.hover"
            : "transparent",
      }}
    >
      <Stack spacing={0.25} sx={{ minWidth: 0 }}>
        <Stack direction="row" spacing={0.5} alignItems="center">
          <Typography
            typography="s2"
            fontWeight={500}
            noWrap
            sx={{ fontFamily: "monospace" }}
            color={row.unmapped ? "warning.dark" : "text.primary"}
          >
            {row.target}
          </Typography>
          {row.auto?.draft && !o && <DraftChip />}
        </Stack>
        <PreviewValue value={row.value} unmapped={row.unmapped} />
      </Stack>
      <Stack spacing={0.5} sx={{ minWidth: 0 }}>
        <Stack direction="row" spacing={0.75} alignItems="center">
          <ModeIcon mode={mode} />
          <Box
            sx={{
              flex: 1,
              minWidth: 0,
              // IT-R2 G5 P3: an auto preset reads as a value, not a grey hint.
              ...(!o && row.auto && !row.auto.empty
                ? {
                    "& input::placeholder": {
                      color: `${theme.palette.text.primary} !important`,
                      opacity: "1 !important",
                    },
                  }
                : {}),
            }}
          >
            {mode === "path" ? (
              <PathInput
                value={o?.path ?? ""}
                sampleRow={sampleRoot ?? undefined}
                placeholder="e.g. turns[0].content"
                onChange={(value, parsed) =>
                  onOverride({
                    mode: "path",
                    path: value,
                    error:
                      value.trim() && parsed && !parsed.ok
                        ? "Invalid path"
                        : null,
                  })
                }
              />
            ) : mode === "empty" ? (
              <Typography typography="s2" color="text.secondary">
                Leave empty
              </Typography>
            ) : (
              <FieldToColumnPicker
                label=""
                placeholder={row.auto?.label ?? "Pick a field"}
                value={o?.field ?? ""}
                options={[
                  ...fieldOptions.map((f) => ({
                    label: f.label,
                    value: f.field,
                  })),
                  { label: "Path (JSONPath)…", value: PATH },
                  { label: "Leave empty", value: EMPTY },
                ]}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === PATH)
                    onOverride({ mode: "path", path: "", error: null });
                  else if (v === EMPTY) onOverride({ mode: "empty" });
                  else if (v) onOverride({ mode: "field", field: v });
                }}
              />
            )}
          </Box>
          {o ? reset : null}
        </Stack>
        {row.unmapped && (
          <Button
            size="small"
            variant="text"
            color="warning"
            onClick={() => onOverride({ mode: "empty" })}
            sx={{
              alignSelf: "flex-start",
              px: 0.5,
              minWidth: 0,
              typography: "s3",
            }}
          >
            No value in these items · Leave empty
          </Button>
        )}
      </Stack>
    </Box>
  );
};
MappingRow.propTypes = {
  row: PropTypes.object.isRequired,
  active: PropTypes.bool,
  onActive: PropTypes.func,
  onOverride: PropTypes.func,
  fieldOptions: PropTypes.array,
  sampleRoot: PropTypes.object,
};

/**
 * The column mapping (R2-SPEC §7.3.1): one line by default (`Fills
 * instruction, … · 0 unmapped` + `Edit mapping`); open, a table of `target
 * column · from · preview of row 1` with `Field ▾` / `Path` / `Leave empty`,
 * recipe chips for the active row and `Remember this mapping`.
 */
const MappingPane = ({
  rows,
  line,
  open,
  onToggle,
  overrides,
  onOverride,
  activeTarget,
  onActive,
  sourceKind,
  sampleRoot,
  remember,
  onRemember,
  rememberLabel,
  loading,
  extraChip,
}) => {
  const unmapped = rows.filter((r) => r.unmapped).length;
  const fieldOptions = FIELD_OPTIONS.filter((f) =>
    f.sources.includes(sourceKind),
  );
  const recipes = RECIPES.filter((r) =>
    fieldOptions.some((f) => f.field === r.field),
  );
  const edited = Object.keys(overrides ?? {}).length;
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Iconify
          icon={
            unmapped ? "mdi:alert-circle-outline" : "mdi:check-circle-outline"
          }
          width={16}
          sx={{ color: unmapped ? "warning.main" : "green.600", flexShrink: 0 }}
        />
        <CustomTooltip show title={line} size="small" arrow>
          <Typography
            typography="s2"
            color="text.primary"
            sx={{ flex: 1, minWidth: 0, opacity: loading ? 0.6 : 1 }}
            data-testid="add-data-automap"
          >
            {line}
          </Typography>
        </CustomTooltip>
        <Button
          size="small"
          variant="text"
          onClick={onToggle}
          endIcon={
            <Iconify
              icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={16}
            />
          }
          sx={{ flexShrink: 0 }}
        >
          {edited
            ? `Edit mapping · ${edited} changed`
            : unmapped && !open
              ? "Map"
              : "Edit mapping"}
        </Button>
      </Stack>
      {extraChip ? <Box sx={{ pl: 3 }}>{extraChip}</Box> : null}
      <Collapse in={open} unmountOnExit>
        <Stack spacing={1}>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "140px 1fr",
              gap: 1.5,
              px: 1,
            }}
          >
            {["Target column · row 1", "From"].map((h) => (
              <Typography key={h} typography="s3" color="text.disabled">
                {h}
              </Typography>
            ))}
          </Box>
          <Stack spacing={0.25}>
            {rows.map((row) => (
              <MappingRow
                key={row.target}
                row={row}
                active={row.target === activeTarget}
                onActive={() => onActive(row.target)}
                onOverride={(o) => onOverride(row.target, o)}
                fieldOptions={fieldOptions}
                sampleRoot={sampleRoot}
              />
            ))}
          </Stack>
          {recipes.length > 0 && (
            <Stack spacing={0.5}>
              <Typography typography="s3" color="text.disabled">
                Recipes · fill {activeTarget ?? "the selected column"}
              </Typography>
              <Stack direction="row" flexWrap="wrap" gap={0.5}>
                {recipes.map((r) => (
                  <Chip
                    key={r.field}
                    label={r.label}
                    size="small"
                    variant="outlined"
                    disabled={!activeTarget}
                    onClick={() =>
                      activeTarget &&
                      onOverride(activeTarget, {
                        mode: "field",
                        field: r.field,
                      })
                    }
                    sx={{ typography: "s3" }}
                  />
                ))}
              </Stack>
            </Stack>
          )}
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={remember}
                onChange={(e) => onRemember(e.target.checked)}
              />
            }
            label={
              <Typography typography="s2" color="text.secondary">
                {rememberLabel}
              </Typography>
            }
          />
        </Stack>
      </Collapse>
    </Stack>
  );
};

MappingPane.propTypes = {
  rows: PropTypes.array.isRequired,
  line: PropTypes.string,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  overrides: PropTypes.object,
  onOverride: PropTypes.func,
  activeTarget: PropTypes.string,
  onActive: PropTypes.func,
  sourceKind: PropTypes.string,
  sampleRoot: PropTypes.object,
  remember: PropTypes.bool,
  onRemember: PropTypes.func,
  rememberLabel: PropTypes.string,
  loading: PropTypes.bool,
  extraChip: PropTypes.node,
};

/**
 * `What to include (8)` for traces (R2-SPEC §7.3.3), collapsed by default,
 * with the size estimate and the dedupe switch.
 */
export const IncludeFlags = ({
  includes,
  onChange,
  open,
  onToggle,
  estimate,
  dedupe,
  onDedupeChange,
}) => (
  <Stack spacing={0.5}>
    <Stack direction="row" alignItems="center" spacing={1}>
      <Button
        size="small"
        variant="text"
        onClick={onToggle}
        endIcon={
          <Iconify
            icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={16}
          />
        }
        sx={{ px: 0.5 }}
      >
        {`What to include (${INCLUDE_FLAGS.length})`}
      </Button>
      <Typography typography="s3" color="text.disabled" noWrap>
        {estimate}
      </Typography>
    </Stack>
    <Collapse in={open} unmountOnExit>
      <Stack spacing={0.5} sx={{ pl: 0.5 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            columnGap: 2,
          }}
        >
          {INCLUDE_FLAGS.map((f) => (
            <FormControlLabel
              key={f.id}
              control={
                <Checkbox
                  size="small"
                  checked={Boolean(includes[f.id])}
                  onChange={(e) => onChange(f.id, e.target.checked)}
                />
              }
              label={<Typography typography="s2">{f.label}</Typography>}
            />
          ))}
        </Box>
        <CustomTooltip
          show
          title={
            dedupe
              ? "Rows already copied from a trace are not added twice."
              : "Traces already copied into a dataset are added again as new rows."
          }
          size="small"
          arrow
        >
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={dedupe}
                disabled={!onDedupeChange}
                onChange={(e) => onDedupeChange?.(e.target.checked)}
              />
            }
            label={
              <Typography typography="s2" color="text.secondary">
                Skip traces already in this dataset (by source trace id)
              </Typography>
            }
          />
        </CustomTooltip>
      </Stack>
    </Collapse>
  </Stack>
);
IncludeFlags.propTypes = {
  includes: PropTypes.object,
  onChange: PropTypes.func,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  estimate: PropTypes.string,
  dedupe: PropTypes.bool,
  onDedupeChange: PropTypes.func,
};

export default MappingPane;
