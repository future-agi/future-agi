import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import { capForKey, keymapEntry, localiseCap } from "src/utils/keymap";
import { useDatasetVersions } from "src/api/develop/dataset-versions";
import SplitChips from "src/sections/common/Datasets/SplitChips";
import { PanelIconButton } from "src/sections/common/RowPanel/RowPanelShell";
import { withKeyHint } from "src/utils/keymap";
import RowMoreMenu from "./RowMoreMenu";
import AssignSplitsMenu from "../Splits/AssignSplitsMenu";
import { AddSplitChip, Pill, SourcePill } from "./RowHeaderPills";
import { isSurfaceShown } from "src/launch/launchSwitch";
import {
  expectedSourceLabel,
  expectedSourceTooltip,
  versionPillText,
  versionPillTooltip,
} from "./rowModel";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { fmtCount } from "src/sections/common/format/fmt";
import { VOCAB } from "src/sections/common/vocab";
import {
  isExpiredSource,
  rowKindOf,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import { runsFlags } from "./runsModel";
import { shownRowSource } from "./rowSource";
import RowCollabPills from "src/sections/common/Collab/RowCollabPills";

const HEADER_BTN_SX = {
  height: 28,
  px: 1,
  minWidth: 0,
  typography: "s2",
  fontWeight: 500,
  whiteSpace: "nowrap",
};

/**
 * Line 1 actions (RV#46, one view A1-19): `Edit` (E) and `Annotate` (A) —
 * labelled when the window has room (≥ 1440 px), icons otherwise — then
 * Copy link (C) and the `?` sheet. Expand and the ⋯ menu come from the
 * shell after these.
 */
export const RowHeaderActions = ({
  actions,
  anchorRef,
  labelled = false,
  canEdit = false,
  editReason = null,
  viewer = false,
  onEdit,
  onAnnotate,
  onHelp,
}) => (
  <Box
    ref={anchorRef}
    sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
  >
    {/* W4 §9 (M6): without edit access, Edit shows disabled with the
        server's reason instead of disappearing. */}
    {!canEdit && editReason ? (
      <CustomTooltip show size="small" title={editReason}>
        <Box component="span" sx={{ display: "inline-flex" }}>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled
            startIcon={<Iconify icon="mdi:pencil-outline" width={15} />}
            sx={{ ...HEADER_BTN_SX, borderColor: "divider" }}
            data-testid="row-edit-denied"
          >
            Edit
          </Button>
        </Box>
      </CustomTooltip>
    ) : null}
    {canEdit &&
      (labelled ? (
        <KeyHintTooltip title="Edit the first expected field" action="edit">
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onEdit}
            startIcon={<Iconify icon="mdi:pencil-outline" width={15} />}
            sx={{ ...HEADER_BTN_SX, borderColor: "divider" }}
            data-testid="row-edit-button"
          >
            Edit
          </Button>
        </KeyHintTooltip>
      ) : (
        <PanelIconButton
          title={withKeyHint("Edit", "edit")}
          hintTitle="Edit the first expected field"
          action="edit"
          label="Edit"
          icon="mdi:pencil-outline"
          onClick={onEdit}
        />
      ))}
    {!viewer &&
      (labelled ? (
        <KeyHintTooltip
          title="Add this row to an annotation queue"
          action="addToQueue"
        >
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onAnnotate}
            startIcon={<Iconify icon="mdi:account-check-outline" width={15} />}
            sx={{ ...HEADER_BTN_SX, borderColor: "divider", mr: 0.5 }}
            data-testid="row-annotate-button"
          >
            Annotate
          </Button>
        </KeyHintTooltip>
      ) : (
        <PanelIconButton
          title={withKeyHint("Annotate", "addToQueue")}
          hintTitle="Add this row to an annotation queue"
          action="addToQueue"
          label="Annotate"
          icon="mdi:account-check-outline"
          onClick={onAnnotate}
        />
      ))}
    <PanelIconButton
      title={withKeyHint("Copy link", "copyLink")}
      hintTitle="Copy link to this row"
      action="copyLink"
      label="Copy link"
      icon="mdi:link-variant"
      onClick={actions.copyLink}
    />
    <PanelIconButton
      title="Keyboard shortcuts"
      hintTitle="Keyboard shortcuts"
      action="help"
      label="Keyboard shortcuts"
      icon="mdi:keyboard-outline"
      onClick={onHelp}
    />
  </Box>
);

RowHeaderActions.propTypes = {
  actions: PropTypes.object.isRequired,
  anchorRef: PropTypes.object,
  labelled: PropTypes.bool,
  editReason: PropTypes.string,
  canEdit: PropTypes.bool,
  viewer: PropTypes.bool,
  onEdit: PropTypes.func,
  onAnnotate: PropTypes.func,
  onHelp: PropTypes.func,
};

/**
 * RV#5: `#32 of 248` beside `Row 33`, muted, with the difference
 * explained; `#3 of 71 filtered` while filters apply.
 */
export const RowPositionText = ({ n, position }) => {
  const total = position?.total;
  if (!Number.isFinite(total)) return null;
  const at = Number.isFinite(position.index) ? `#${position.index}` : "Outside";
  const filtered = position.filtered;
  const text = `${at} of ${fmtCount(total)}${filtered ? " filtered" : ""}`;
  const tip = Number.isFinite(position.index)
    ? `Row ${n} is its permanent number. #${position.index} is its position in the current view${
        filtered ? " (filters, sort and search apply)" : " (sort applies)"
      }.`
    : `Row ${n} is not in the current view. Clear the filters to see it in the grid.`;
  return (
    <CustomTooltip show size="small" placement="bottom" arrow title={tip}>
      <Typography
        component="span"
        typography="s2"
        color="text.disabled"
        noWrap
        data-testid="row-position"
        sx={{
          ml: 0.75,
          flexShrink: 0,
          fontVariantNumeric: "tabular-nums",
          cursor: "help",
        }}
      >
        {text}
      </Typography>
    </CustomTooltip>
  );
};

RowPositionText.propTypes = {
  n: PropTypes.number,
  position: PropTypes.object,
};

const STRIP = [
  ["next", "next"],
  ["prev", "prev"],
  ["edit", "edit"],
  ["diff", "diff"],
  ["raw", "raw"],
  ["full", "expand"],
];

const capOf = (action) => {
  const entry = keymapEntry(action);
  return entry
    ? localiseCap(entry.display?.[0] ?? capForKey(entry.keys[0]))
    : "";
};

/**
 * The key strip (RV#3; one view A1-18: the row view's footer Hint):
 * `J next · K prev · E edit · D diff · R raw · F expand · ? all` — the `?`
 * opens the full sheet.
 */
export const RowKeyStrip = ({ onHelp, readOnly = false }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={1.25}
    data-testid="row-key-strip"
    sx={{ minHeight: 26, overflow: "hidden", whiteSpace: "nowrap" }}
  >
    {STRIP.filter(([action]) => !(readOnly && action === "edit")).map(
      ([action, word]) => (
        <Stack key={action} direction="row" alignItems="center" gap={0.5}>
          <KeyCap sx={{ height: 18, minWidth: 18, px: 0.5, typography: "s3" }}>
            {capOf(action)}
          </KeyCap>
          <Typography typography="s3" color="text.secondary">
            {word}
          </Typography>
        </Stack>
      ),
    )}
    <Box sx={{ flex: 1 }} />
    <Button
      size="small"
      color="inherit"
      onClick={onHelp}
      sx={{ typography: "s3", color: "text.secondary", minWidth: 0, px: 0.5 }}
      endIcon={
        <KeyCap sx={{ height: 18, minWidth: 18, px: 0.5, typography: "s3" }}>
          ?
        </KeyCap>
      }
    >
      All shortcuts
    </Button>
  </Stack>
);

RowKeyStrip.propTypes = { onHelp: PropTypes.func, readOnly: PropTypes.bool };

/** The ⋯ menu (after Expand). */
export const RowHeaderMenu = ({ menuItems, canEdit }) => (
  <RowMoreMenu items={menuItems} canEdit={canEdit} />
);

RowHeaderMenu.propTypes = {
  menuItems: PropTypes.object.isRequired,
  canEdit: PropTypes.bool,
};

/**
 * R2 (P5): the source of an imported session — `From Session 1a2b3c4d` —
 * opens the Conversation tab.
 */
const SessionSourcePill = ({ source, onOpen }) => (
  <Pill
    icon="mdi:forum-outline"
    onClick={onOpen}
    tooltip={`Open the conversation${
      isExpiredSource(source)
        ? " · the original session expired; shows the copy saved at import"
        : ""
    }`}
    testId="row-source-pill"
  >
    From {source.label ?? "a session"}
  </Pill>
);
SessionSourcePill.propTypes = {
  source: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};

/**
 * Line 2: split chips + dashed `+ Split`, the source pill, `Added v1 ·
 * edited v3`, the hard-case flag and the amber `Unsaved edits`. R2: the
 * `Task` / `Conversation` kind pill first.
 */
export const RowHeaderLine2 = ({
  datasetId,
  row,
  splits,
  canEdit,
  onOpenTrace,
  onOpenSourceRun,
  onOpenHistory,
  runsSummary,
  pendingDelete,
  onOpenConversation,
  splitAnchor,
  setSplitAnchor,
  splitChipRef,
  expectedSource,
  idChip = null,
}) => {
  const anchor = splitAnchor;
  const setAnchor = setSplitAnchor;
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const { data: versions } = useDatasetVersions(datasetId);
  const meta = row?.row_meta ?? {};
  const kind = rowKindOf(row, datasetKind);
  const source = shownRowSource(meta.source);
  const sourceKind = source?.kind ?? source?.type;
  const versionText = versionPillText(meta);
  const hard = runsFlags(runsSummary).find((f) => f.key === "hard");
  return (
    <>
      {/* IT-R2 G5 P6: the full id chip (copy) leads line 2 so it never
          clips between the title and the header actions. */}
      {idChip}
      {(kind === "task" || kind === "conversation") && (
        <Pill
          icon={
            kind === "task"
              ? "mdi:clipboard-check-outline"
              : "mdi:forum-outline"
          }
          tooltip={
            kind === "task"
              ? "A task: an opening message, a goal, a start state and Check evals"
              : "A whole conversation imported from a session"
          }
          testId="row-kind-pill"
        >
          {kind === "task" ? VOCAB.taskBadge : VOCAB.conversationBadge}
        </Pill>
      )}
      <SplitChips splits={splits} max={3} />
      {/* L6 launch switch: no splits (dataset.splits) and no comments or
          assignments (dataset.collab.comments, dataset.collab.assignments) on
          dev. */}
      {canEdit && isSurfaceShown("dataset.splits") && (
        <AddSplitChip
          ref={splitChipRef}
          onClick={(e) => setAnchor(e.currentTarget)}
        />
      )}
      {(isSurfaceShown("dataset.collab.comments") ||
        isSurfaceShown("dataset.collab.assignments")) && (
        <RowCollabPills
          datasetId={datasetId}
          rowId={row?.row_id}
          canEdit={canEdit}
        />
      )}
      {datasetId && row?.row_id && (
        <AssignSplitsMenu
          anchorEl={anchor}
          onClose={() => setAnchor(null)}
          datasetId={datasetId}
          rowIds={[row.row_id]}
          rowSplits={[row?.row_meta?.split_ids ?? []]}
        />
      )}
      {sourceKind === "session" ? (
        <SessionSourcePill source={source} onOpen={onOpenConversation} />
      ) : (
        <SourcePill
          source={source}
          createdAt={meta.created_at}
          onOpenTrace={onOpenTrace}
          onOpenRun={onOpenSourceRun}
        />
      )}
      {isSurfaceShown("dataset.versions") && versionText && (
        <Pill
          icon="mdi:history"
          onClick={onOpenHistory}
          tooltip={versionPillTooltip(meta, versions)}
          testId="row-version-pill"
        >
          {versionText}
        </Pill>
      )}
      {expectedSource && (
        <Pill
          icon="mdi:source-branch"
          tooltip={expectedSourceTooltip(expectedSource)}
          testId="row-expected-source"
        >
          {expectedSourceLabel(expectedSource)}
        </Pill>
      )}
      {hard && (
        <Pill tone="red" tooltip={hard.tooltip} testId="row-hard-case">
          {hard.label}
        </Pill>
      )}
      {pendingDelete && (
        <Pill tone="red" tooltip="Deleted when you save the next version">
          Pending delete
        </Pill>
      )}
    </>
  );
};

RowHeaderLine2.propTypes = {
  datasetId: PropTypes.string,
  row: PropTypes.object,
  splits: PropTypes.array.isRequired,
  canEdit: PropTypes.bool,
  onOpenTrace: PropTypes.func,
  onOpenSourceRun: PropTypes.func,
  onOpenHistory: PropTypes.func,
  runsSummary: PropTypes.object,
  pendingDelete: PropTypes.bool,
  onOpenConversation: PropTypes.func,
  splitAnchor: PropTypes.object,
  setSplitAnchor: PropTypes.func,
  splitChipRef: PropTypes.object,
  expectedSource: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  idChip: PropTypes.node,
};
