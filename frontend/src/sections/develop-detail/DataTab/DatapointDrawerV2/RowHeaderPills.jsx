import React, { forwardRef } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  fmtTimeAbsolute,
  fmtTimeRelative,
} from "src/sections/common/format/fmt";
import { KeyHintTitle } from "src/sections/common/ux/KeyHintTooltip";
import { copyToClipboard } from "src/utils/utils";
import { shortId } from "./rowModel";

const PILL_SX = {
  display: "inline-flex",
  alignItems: "center",
  gap: 0.5,
  height: 22,
  px: 0.875,
  borderRadius: 0.75,
  border: "1px solid",
  borderColor: "divider",
  typography: "s2",
  color: "text.secondary",
  whiteSpace: "nowrap",
  maxWidth: 280,
  overflow: "hidden",
  textOverflow: "ellipsis",
};

/** A small outlined pill; a button when `onClick`. */
export const Pill = ({ icon, children, onClick, tooltip, tone, testId }) => {
  const toneSx =
    tone === "amber"
      ? {
          borderColor: "transparent",
          bgcolor: (t) => alpha(t.palette.amber[500], 0.16),
          color: (t) =>
            t.palette.mode === "dark"
              ? t.palette.amber[500]
              : t.palette.amber[700],
          fontWeight: 500,
        }
      : tone === "red"
        ? {
            borderColor: "transparent",
            bgcolor: (t) => alpha(t.palette.red[600], 0.12),
            color: (t) =>
              t.palette.mode === "dark"
                ? t.palette.red[400]
                : t.palette.red[700],
            fontWeight: 500,
          }
        : {};
  const body = (
    <Box
      component={onClick ? ButtonBase : "span"}
      onClick={onClick}
      data-testid={testId}
      sx={{
        ...PILL_SX,
        ...toneSx,
        ...(onClick && { "&:hover": { borderColor: "text.disabled" } }),
      }}
    >
      {icon && <Iconify icon={icon} width={14} sx={{ flexShrink: 0 }} />}
      <Box
        component="span"
        sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {children}
      </Box>
    </Box>
  );
  if (!tooltip) return body;
  return (
    <CustomTooltip show size="small" placement="bottom" arrow title={tooltip}>
      {body}
    </CustomTooltip>
  );
};

Pill.propTypes = {
  icon: PropTypes.string,
  children: PropTypes.node,
  onClick: PropTypes.func,
  tooltip: PropTypes.node,
  tone: PropTypes.oneOf(["amber", "red"]),
  testId: PropTypes.string,
};

/** `b1000000` — copies the full row uuid. */
export const RowIdChip = ({ rowId }) => (
  <CustomTooltip show size="small" placement="bottom" arrow title="Copy row ID">
    <ButtonBase
      aria-label="Copy row ID"
      onClick={() => {
        copyToClipboard(rowId);
        enqueueSnackbar("Row ID copied", { variant: "success" });
      }}
      sx={{
        gap: 0.5,
        height: 22,
        px: 0.75,
        borderRadius: 0.5,
        bgcolor: "background.neutral",
        typography: "s3",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        color: "text.secondary",
        "&:hover": { color: "text.primary" },
      }}
    >
      <Box
        component="span"
        sx={{ color: "text.disabled", fontFamily: "inherit" }}
      >
        ID
      </Box>
      {shortId(rowId)}
      <Iconify icon="tabler:copy" width={12} />
    </ButtonBase>
  </CustomTooltip>
);

RowIdChip.propTypes = { rowId: PropTypes.string.isRequired };

/**
 * Where the row came from: `From Trace 7e1a2b3c · 3d ago` or `From Span
 * 1a2b3c4d · 3d ago` (opens the trace), the upload file, `Added manually`,
 * or `From #6B · Row 31`.
 */
export const SourcePill = ({ source, createdAt, onOpenTrace, onOpenRun }) => {
  if (!source) return null;
  if (source.type === "trace" || source.type === "span") {
    const at = source.captured_at ?? createdAt;
    const what = source.type === "span" ? "span" : "trace";
    return (
      <Pill
        icon="mdi:transit-connection-variant"
        onClick={source.trace_id ? onOpenTrace : undefined}
        tooltip={
          source.expired
            ? `The original ${what} expired; opens the copy saved at import`
            : `Open the source trace${at ? ` · imported ${fmtTimeAbsolute(at)}` : ""}`
        }
        testId="row-source-pill"
      >
        From {source.label ?? what}
        {at ? ` · ${fmtTimeRelative(at)}` : ""}
      </Pill>
    );
  }
  if (source.type === "experiment") {
    return (
      <Pill
        icon="material-symbols:science-outline"
        onClick={onOpenRun}
        tooltip="Open the experiment result this row came from"
        testId="row-source-pill"
      >
        From {source.run_label ?? "an experiment"}
        {source.row ? ` · Row ${source.row}` : ""}
      </Pill>
    );
  }
  if (source.type === "upload") {
    return (
      <Pill
        icon="mdi:file-delimited-outline"
        tooltip={`Imported from ${source.file_name ?? source.label ?? "a file"}${
          createdAt ? ` · ${fmtTimeAbsolute(createdAt)}` : ""
        }`}
        testId="row-source-pill"
      >
        {source.file_name ?? source.label ?? "Upload"}
      </Pill>
    );
  }
  return (
    <Pill
      icon="mdi:account-edit-outline"
      tooltip={createdAt ? `Added ${fmtTimeAbsolute(createdAt)}` : undefined}
      testId="row-source-pill"
    >
      {source.label ? `Added manually · ${source.label}` : "Added manually"}
    </Pill>
  );
};

SourcePill.propTypes = {
  source: PropTypes.object,
  createdAt: PropTypes.string,
  onOpenTrace: PropTypes.func,
  onOpenRun: PropTypes.func,
};

/** Dashed `+ Split` trigger (tooltip `Assign splits  [S]`). */
export const AddSplitChip = forwardRef(function AddSplitChip(
  { onClick, disabled },
  ref,
) {
  return (
    <CustomTooltip
      show
      size="small"
      placement="bottom"
      arrow
      disableInteractive
      title={
        <KeyHintTitle
          title="Assign splits"
          action="splits"
          description="Changes are saved immediately"
        />
      }
    >
      <ButtonBase
        ref={ref}
        onClick={onClick}
        disabled={disabled}
        aria-label="Assign splits"
        sx={{
          height: 20,
          px: 0.75,
          gap: 0.25,
          borderRadius: 0.5,
          border: "1px dashed",
          borderColor: "text.disabled",
          color: "text.secondary",
          typography: "s3",
          fontWeight: 500,
          "&:hover": { borderColor: "primary.main", color: "primary.main" },
          "&.Mui-disabled": { opacity: 0.5 },
        }}
      >
        <Iconify icon="mdi:plus" width={12} />
        <Typography component="span" typography="s3" fontWeight={500}>
          Split
        </Typography>
      </ButtonBase>
    </CustomTooltip>
  );
});

AddSplitChip.propTypes = { onClick: PropTypes.func, disabled: PropTypes.bool };

/**
 * RV#6: the pending-edit marker on the title line (line 1 never wraps, so
 * it appears without moving anything).
 */
export const UnsavedPill = () => (
  <Box sx={{ ml: 0.75, flexShrink: 0 }}>
    <Pill
      tone="amber"
      tooltip={
        <KeyHintTitle
          title="Unsaved edits on this row. They are saved with the next version."
          action="saveVersion"
        />
      }
      testId="row-unsaved-pill"
    >
      Unsaved
    </Pill>
  </Box>
);

/**
 * IT-R3 G5 P1: an imported row before Save version — `Unsaved · in draft
 * v7` + [Save version]. The row reads from the pending change set; it is
 * read-only until the version that adds it is saved.
 */
export const DraftRowChip = ({ version, onSave, canSave = true }) => (
  <Box
    sx={{ ml: 0.75, flexShrink: 0, display: "inline-flex", gap: 0.5 }}
    data-testid="row-draft-chip"
  >
    <Pill
      tone="amber"
      icon="mdi:file-document-edit-outline"
      tooltip={`Imported into draft v${version}. It is read-only here and gets a row number when you save v${version}.`}
    >
      {`Unsaved · in draft v${version}`}
    </Pill>
    {canSave && onSave ? (
      <ButtonBase
        onClick={onSave}
        data-testid="row-draft-save"
        sx={{
          ...PILL_SX,
          color: "primary.main",
          borderColor: "primary.main",
          fontWeight: 600,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        Save version
      </ButtonBase>
    ) : null}
  </Box>
);

DraftRowChip.propTypes = {
  version: PropTypes.number,
  onSave: PropTypes.func,
  canSave: PropTypes.bool,
};
