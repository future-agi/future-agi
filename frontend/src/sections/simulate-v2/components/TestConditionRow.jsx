import PropTypes from "prop-types";
import { useState } from "react";
import { Button, MenuItem, Stack, TextField, Typography } from "@mui/material";
import {
  CALLS,
  CONDITIONS,
  conditionLabelOf,
  conditionSupportOf,
  isRetiredKind,
  kindLabelOf,
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
    "The tool refuses until someone approves — the agent has to ask for it.",
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
  /* "first" | "every" | "nth", with the number kept beside it. */
  const [call, setCall] = useState(
    typeof current?.call === "number" ? "nth" : current?.call || "first",
  );
  const [nth, setNth] = useState(
    typeof current?.call === "number" ? String(current.call) : "2",
  );
  const support = conditionSupportOf(env, envState);
  /* A kind saved before the five: shown as it was, not offered again. */
  const retired = isRetiredKind(kind);
  const n = Number(nth);
  const nthOk = Number.isInteger(n) && n >= 2 && n <= 99;
  const savedCall = call === "nth" ? n : call;

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
          label="Which call"
          value={call}
          onChange={(e) => setCall(e.target.value)}
          sx={{ minWidth: 150 }}
        >
          {CALLS.map((c) => (
            <MenuItem key={c.id} value={c.id}>
              {c.label}
            </MenuItem>
          ))}
        </TextField>
        {call === "nth" && (
          <TextField
            size="small"
            type="number"
            label="Call number"
            value={nth}
            onChange={(e) => setNth(e.target.value)}
            error={!nthOk}
            inputProps={{ min: 2, max: 99, "aria-label": "Call number" }}
            sx={{ width: 110 }}
          />
        )}
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!tool || retired || (call === "nth" && !nthOk)}
          title={retired ? "Pick one of the conditions on offer" : ""}
          onClick={() => {
            onChange({
              ...row,
              testCondition: { tool, kind, call: savedCall },
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
          : `${KIND_NOTE[kind] || ""} ${
              call === "every"
                ? "Every call to it fails this way."
                : call === "nth"
                  ? nthOk
                    ? `Calls before number ${n} behave normally.`
                    : "Call number is 2 or more."
                  : "Only the first call fails; later ones behave normally."
            }`.trim()}
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
