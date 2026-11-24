import PropTypes from "prop-types";
import { useState } from "react";
import { Button, MenuItem, Stack, TextField, Typography } from "@mui/material";
import {
  APPROVAL_MISSING,
  CALLS,
  CONDITIONS,
  approvalActionsForConditions,
  conditionLabelOf,
  conditionSupportOf,
  isRetiredKind,
  kindLabelOf,
  needsApprovalAction,
  normalizeCondition,
} from "../_mock/testConditions";

/* What each kind does to the call, in one line under the picker. */
const KIND_NOTE = {
  timeout:
    "The call never runs. The agent sees a timeout and nothing has changed.",
  after_commit:
    "The call runs and a write lands, but the agent never gets the answer. A retry that writes twice fails.",
  unavailable: "The tool can't be reached at all — every attempt fails fast.",
  error: "The tool answers, with an error instead of a result.",
  permission:
    "The tool refuses until the approval action is recorded — the agent has to ask for it.",
};

/*
  One scenario's tool behaviour: none, or one tool it uses misbehaving on
  purpose. Hidden from the agent, like the rest of how it's marked.
*/
export default function TestConditionRow({
  row,
  env,
  envState,
  tools,
  locked,
  onChange,
}) {
  const current = normalizeCondition(row.testCondition);
  const [editing, setEditing] = useState(false);
  const [tool, setTool] = useState(current?.tool || tools[0] || "");
  const [kind, setKind] = useState(current?.kind || CONDITIONS[0].kind);
  /* One call number — never every call. An older "every" save opens on
     call 1, the call it always hit first. */
  const [call, setCall] = useState(current?.call || 1);
  /* "Needs approval first" names the approval action that records it. */
  const approvalActions = approvalActionsForConditions(env, envState);
  const [approval, setApproval] = useState(
    current?.approval ||
      (approvalActions.length === 1 ? approvalActions[0] : ""),
  );
  const support = conditionSupportOf(env, envState);
  /* A kind saved before the five: shown as it was, not offered again. */
  const retired = isRetiredKind(kind);
  const needsApproval = needsApprovalAction(kind);
  const approvalMissing = needsApproval && !approval;
  /* A saved call beyond the list stays pickable. */
  const callOptions = CALLS.some((c) => c.id === call)
    ? CALLS
    : [...CALLS, { id: call, label: `Call ${call}` }];

  if (!editing)
    return (
      <Stack spacing={0.5}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography className="sd-val" sx={{ flex: 1, minWidth: 0 }}>
            {current
              ? `${conditionLabelOf(current)} — injected on every run of this scenario`
              : "Tools behave normally"}
          </Typography>
          {!locked && onChange && (
            <Button
              variant="outlined"
              size="small"
              disabled={!support.ok || !tools.length}
              title={
                support.reason ||
                (!tools.length ? "This scenario uses no tools" : "")
              }
              onClick={() => setEditing(true)}
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {current ? "Change" : "Add a condition"}
            </Button>
          )}
        </Stack>
        {!support.ok && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {support.reason}
          </Typography>
        )}
      </Stack>
    );

  return (
    <Stack spacing={0.75}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
      >
        <TextField
          select
          size="small"
          label="Tool"
          value={tool}
          onChange={(e) => setTool(e.target.value)}
          sx={{ minWidth: 180 }}
        >
          {tools.map((t) => (
            <MenuItem key={t} value={t}>
              {t}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Condition"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          sx={{ minWidth: 220 }}
          SelectProps={{
            renderValue: (v) =>
              isRetiredKind(v)
                ? `${kindLabelOf(v)} (no longer offered)`
                : kindLabelOf(v),
          }}
        >
          {CONDITIONS.map((c) => (
            <MenuItem key={c.kind} value={c.kind}>
              {c.label}
            </MenuItem>
          ))}
          {retired && (
            <MenuItem value={kind} disabled>
              {`${kindLabelOf(kind)} (no longer offered)`}
            </MenuItem>
          )}
        </TextField>
        <TextField
          select
          size="small"
          label="On which call"
          value={call}
          onChange={(e) => setCall(Number(e.target.value))}
          inputProps={{ "aria-label": "On which call" }}
          sx={{ minWidth: 120 }}
        >
          {callOptions.map((c) => (
            <MenuItem key={c.id} value={c.id}>
              {c.label}
            </MenuItem>
          ))}
        </TextField>
        {needsApproval && (
          <TextField
            select
            size="small"
            label="Approval it needs"
            value={approval}
            onChange={(e) => setApproval(e.target.value)}
            error={approvalMissing}
            SelectProps={{
              displayEmpty: true,
              renderValue: (v) => v || "Pick an action",
            }}
            InputLabelProps={{ shrink: true }}
            inputProps={{ "aria-label": "Approval it needs" }}
            sx={{
              minWidth: 200,
              "& .MuiSelect-select": {
                fontFamily: "ui-monospace, Menlo, monospace",
              },
            }}
          >
            {approvalActions.length === 0 && (
              <MenuItem value="" disabled>
                This environment has no approval actions
              </MenuItem>
            )}
            {approvalActions.map((a) => (
              <MenuItem
                key={a}
                value={a}
                sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
              >
                {a}
              </MenuItem>
            ))}
            {approval && !approvalActions.includes(approval) && (
              <MenuItem value={approval} disabled>
                {`${approval} (not an action of this world)`}
              </MenuItem>
            )}
          </TextField>
        )}
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!tool || retired || approvalMissing}
          title={
            retired
              ? "Pick one of the conditions on offer"
              : approvalMissing
                ? APPROVAL_MISSING
                : ""
          }
          onClick={() => {
            onChange({
              ...row,
              testCondition: {
                tool,
                kind,
                call,
                ...(needsApproval && { approval }),
              },
            });
            setEditing(false);
          }}
        >
          Save
        </Button>
        {current && (
          <Button
            variant="outlined"
            size="small"
            onClick={() => {
              const { testCondition, ...rest } = row; // eslint-disable-line no-unused-vars
              onChange(rest);
              setEditing(false);
            }}
          >
            Remove
          </Button>
        )}
        <Button
          size="small"
          onClick={() => setEditing(false)}
          sx={{ color: "text.secondary" }}
        >
          Cancel
        </Button>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {retired
          ? "This condition is no longer offered — pick one of the five to save."
          : approvalMissing
            ? `${APPROVAL_MISSING}.`
            : `${KIND_NOTE[kind] || ""} ${
                call > 1
                  ? `Only call ${call} fails; the calls before and after behave normally.`
                  : "Only the first call fails; later ones behave normally."
              }${current?.wasEvery ? " It was saved as every call, which isn't run — it now hits one call." : ""}`.trim()}
      </Typography>
    </Stack>
  );
}
TestConditionRow.propTypes = {
  row: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  tools: PropTypes.array.isRequired,
  locked: PropTypes.bool,
  onChange: PropTypes.func,
};
