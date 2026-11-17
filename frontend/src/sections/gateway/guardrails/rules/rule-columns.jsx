import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Switch from "@mui/material/Switch";

import { GatewayStatusChip } from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { KIND_LABELS, STAGE_LABELS } from "../catalog";
import { EMPTY } from "../../utils/formatters";
import { ActionChip, RuleCell } from "./RuleCells";
import {
  GUARDRAILS_OFF_REASON,
  NOT_ENFORCED_STATUS,
} from "../../utils/guardrailPipeline";

// Cells inside the row that act on their own carry this attribute, so a
// click on them does not also open the row (AG Grid sees the native click
// before React does, so stopPropagation cannot help).
export const ROW_ACTION = "data-row-action";

// A column that keeps its width and lets the Rule column take the rest.
// Each holds its header with the sort arrow, and its widest value. The grid
// fits from about 1360 px; below that the middle columns scroll under the
// On/Status column, which stays pinned right, so the switch stays on screen.
const fixed = (width) => ({ width, minWidth: width, flex: 0 });

// `canEdit`: switching a rule on or off and setting one up change the
// organization's gateway config, which only an Owner or Admin may do
// (useGatewayAdmin); for anyone else both show disabled and say why.
// 850(a): while the pipeline is off, show Not enforced alongside the saved
// enabled switch, not instead of it. The stored flag is applied once on.
export function ruleColumns({
  onToggle,
  onSetUp,
  busy,
  canEdit = true,
  enforced = true,
}) {
  function StatusCell({ data }) {
    if (data.storedOnly) {
      return (
        <GatewayStatusChip
          tone={enforced && data.enabled ? "healthy" : "neutral"}
          label={!enforced ? NOT_ENFORCED_STATUS : data.enabled ? "On" : "Off"}
          showIcon={false}
        />
      );
    }
    if (data.needsSetup) {
      return (
        <GatewayAdminOnly allowed={canEdit}>
          <Button
            size="small"
            variant="outlined"
            disabled={!canEdit}
            {...{ [ROW_ACTION]: true }}
            onClick={() => onSetUp(data)}
          >
            Set up
          </Button>
        </GatewayAdminOnly>
      );
    }
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          justifyContent: "center",
          height: "100%",
          lineHeight: 1,
        }}
      >
        {!enforced && (
          <Box
            component="span"
            title={GUARDRAILS_OFF_REASON}
            sx={{ display: "inline-flex" }}
          >
            <GatewayStatusChip
              tone="neutral"
              label={NOT_ENFORCED_STATUS}
              showIcon={false}
            />
          </Box>
        )}
        <GatewayAdminOnly allowed={canEdit}>
          <Switch
            size="small"
            checked={data.enabled}
            disabled={busy || !canEdit}
            // NEW: "<rule> saved on" distinguishes stored state from enforcement.
            inputProps={{
              "aria-label": `${data.label}${enforced ? " on" : " saved on"}`,
            }}
            {...{ [ROW_ACTION]: true }}
            onChange={(event) => onToggle(data, event.target.checked)}
          />
        </GatewayAdminOnly>
      </Box>
    );
  }
  StatusCell.propTypes = { data: PropTypes.object.isRequired };

  return [
    {
      field: "label",
      headerName: "Rule",
      cellRenderer: RuleCell,
      minWidth: 220,
      flex: 3,
    },
    {
      field: "kind",
      headerName: "Type",
      valueFormatter: ({ value }) => KIND_LABELS[value] || value,
      ...fixed(100),
    },
    {
      field: "stage",
      headerName: "Runs on",
      valueFormatter: ({ value }) => STAGE_LABELS[value] || value,
      ...fixed(112),
    },
    {
      field: "action",
      headerName: "Action",
      cellRenderer: ({ value }) => <ActionChip action={value} />,
      ...fixed(101),
    },
    {
      field: "threshold",
      headerName: "Threshold",
      headerTooltip: "A match scoring below this is ignored",
      valueFormatter: ({ value }) =>
        value === null || value === undefined
          ? EMPTY
          : Number(value).toFixed(2),
      ...fixed(125),
      type: "rightAligned",
    },
    {
      field: "triggers",
      headerName: "Matches",
      headerTooltip: "Requests this rule matched in the last 7 days",
      ...fixed(115),
      type: "rightAligned",
    },
    {
      field: "blocked",
      headerName: "Blocked",
      headerTooltip: "Requests this rule blocked in the last 7 days",
      ...fixed(111),
      type: "rightAligned",
    },
    {
      field: "accuracy",
      headerName: "Accuracy",
      headerTooltip: "Reviews marked correct in Feedback, out of all reviews",
      valueFormatter: ({ value }) =>
        value === null || value === undefined ? EMPTY : `${Math.round(value)}%`,
      ...fixed(122),
      type: "rightAligned",
    },
    {
      colId: "status",
      field: "enabled",
      headerName: enforced ? "On" : "Status",
      cellRenderer: StatusCell,
      sortable: false,
      // Room for Not enforced (120 px) and the Set up button; pinned so the
      // switch stays on screen when the middle columns scroll.
      ...fixed(122),
      pinned: "right",
    },
  ];
}
