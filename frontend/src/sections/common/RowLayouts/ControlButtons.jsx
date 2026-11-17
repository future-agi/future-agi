// Row layouts (W9-4): write-back buttons. On the Row tab each button adds
// one pending change (useChangeSet(datasetId).addOp) with the layout as its
// source; Save version commits it like any other edit, and the toast's Undo
// takes back exactly that change (by its op id). The Customize panel's
// Buttons section is ButtonsEditor.jsx. A kind shows only when the
// change-set has its op (set_status / set_tags come with W3 A).
import React from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { notify } from "src/sections/common/ux/toast";
import { useChangeSet } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import {
  controlOp,
  controlsDisabledReason,
  layoutOpId,
  runUndo,
  undoPlan,
} from "./controlOps";
import { availableControlKinds, rowStatusLabels } from "./changeSetKinds";

export { default as ButtonsEditor } from "./ButtonsEditor";

const same = (a, b) => String(a ?? "") === String(b ?? "");

/**
 * The layout's buttons on one row.
 * @param {{controls: object[], columns: object[], cells: object,
 *   datasetId: string, rowId: string, rowLabel: string,
 *   layout: {id: string, name: string}, viewer?: boolean,
 *   asOfVersion?: string|number|null, pendingDelete?: boolean,
 *   rowStatus?: {status: string, reason?: string}|null}} props
 *   `rowStatus` = the row's saved status (W3 A `row_meta`).
 */
const ControlButtons = ({
  controls,
  columns,
  cells,
  datasetId,
  rowId,
  rowLabel,
  layout,
  viewer,
  asOfVersion,
  pendingDelete,
  rowStatus,
}) => {
  const changeSet = useChangeSet(datasetId);
  const kinds = availableControlKinds();
  const statusLabel = rowStatusLabels();
  const runnable = (controls ?? []).filter((c) => kinds.includes(c.kind));
  if (!runnable.length) return null;
  const reason = controlsDisabledReason({ viewer, asOfVersion, pendingDelete });
  const press = (control) => {
    const cur = cells?.[control.column_id];
    if (control.kind === "set_field" && cur && same(cur.value, control.value)) {
      const col = columns.find((c) => c.id === control.column_id);
      notify(`${rowLabel} · ${col?.name ?? "This field"} already reads that`, {
        key: `layout-btn-${control.id}`,
      });
      return;
    }
    if (control.kind === "set_status") {
      const now = changeSet.pendingStatus?.[rowId] ?? rowStatus;
      if (
        now?.status === control.status &&
        same(now?.reason, String(control.reason ?? "").trim())
      ) {
        notify(
          `${rowLabel} is already ${statusLabel?.[control.status] ?? control.status}`,
          { key: `layout-btn-${control.id}` },
        );
        return;
      }
    }
    const res = controlOp(control, {
      rowId,
      rowLabel,
      columns,
      original: (id) => cells?.[id]?.original,
      layout,
      statusLabel,
    });
    if (res.error) {
      notify(res.error, { variant: "error", key: `layout-btn-${control.id}` });
      return;
    }
    const op = { ...res.op, id: layoutOpId() };
    const plan = undoPlan(changeSet.ops, op);
    changeSet.addOp(op);
    notify(res.toast, {
      variant: "success",
      key: `layout-btn-${control.id}`,
      ...(plan ? { undo: { onUndo: () => runUndo(changeSet, plan) } } : {}),
    });
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      flexWrap="wrap"
      data-testid="layout-buttons"
    >
      {runnable.map((c) => (
        <CustomTooltip
          key={c.id}
          show={Boolean(reason)}
          title={reason ?? ""}
          size="small"
          arrow
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              disabled={Boolean(reason)}
              onClick={() => press(c)}
              data-testid={`layout-btn-${c.label}`}
              sx={{ typography: "s2" }}
            >
              {c.label}
            </Button>
          </span>
        </CustomTooltip>
      ))}
      {reason && (
        <Typography
          typography="s3"
          color="text.disabled"
          data-testid="layout-buttons-reason"
        >
          {reason}
        </Typography>
      )}
    </Stack>
  );
};

ControlButtons.propTypes = {
  controls: PropTypes.array,
  columns: PropTypes.array.isRequired,
  cells: PropTypes.object,
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  rowLabel: PropTypes.string.isRequired,
  layout: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string })
    .isRequired,
  viewer: PropTypes.bool,
  asOfVersion: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  pendingDelete: PropTypes.bool,
  rowStatus: PropTypes.shape({
    status: PropTypes.string,
    reason: PropTypes.string,
  }),
};

export default ControlButtons;
