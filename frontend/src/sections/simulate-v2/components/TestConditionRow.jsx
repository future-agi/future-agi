import PropTypes from "prop-types";
import { useState } from "react";
import { Button, MenuItem, Stack, TextField, Typography } from "@mui/material";
import {
  COMMIT_POINTS,
  CONDITIONS,
  conditionLabelOf,
  conditionSupportOf,
  takesCommitPoint,
} from "../_mock/testConditions";

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
  const current = row.testCondition || null;
  const [editing, setEditing] = useState(false);
  const [tool, setTool] = useState(current?.tool || tools[0] || "");
  const [kind, setKind] = useState(current?.kind || CONDITIONS[0].kind);
  const [commit, setCommit] = useState(current?.commit || "before");
  const support = conditionSupportOf(env, envState);
  const pointed = takesCommitPoint(tool, kind);

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
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <TextField
          select
          size="small"
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
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          sx={{ minWidth: 180 }}
        >
          {CONDITIONS.map((c) => (
            <MenuItem key={c.kind} value={c.kind}>
              {c.label}
            </MenuItem>
          ))}
        </TextField>
        {pointed && (
          <TextField
            select
            size="small"
            value={commit}
            onChange={(e) => setCommit(e.target.value)}
            sx={{ minWidth: 200 }}
          >
            {COMMIT_POINTS.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.label}
              </MenuItem>
            ))}
          </TextField>
        )}
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!tool}
          onClick={() => {
            onChange({
              ...row,
              testCondition: { tool, kind, ...(pointed && { commit }) },
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
      {pointed && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {commit === "after"
            ? "The write lands, but the agent only sees a timeout. A retry that creates a second record fails."
            : "The write never lands. The agent sees a timeout and nothing has changed."}
        </Typography>
      )}
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
