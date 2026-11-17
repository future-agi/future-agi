import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, MenuItem, TextField, Typography } from "@mui/material";
import SectionTitle from "../components/SectionTitle";
import { approvalForm, approvalPatch } from "./approvalPolicy";

export default function ApprovalPolicyFields({
  policy,
  disabled = false,
  onChange,
}) {
  const [form, setForm] = useState(() => approvalForm(policy));
  const [error, setError] = useState("");
  useEffect(() => {
    setForm(approvalForm(policy));
    setError("");
  }, [policy]);
  const change = (field, value) => {
    if (disabled) return;
    const next = { ...form, [field]: value };
    setForm(next);
    try {
      const patch = approvalPatch(next);
      setError("");
      onChange(patch, "");
    } catch (err) {
      setError(err.message);
      onChange(undefined, err.message);
    }
  };
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <SectionTitle
        title="Tool approvals"
        note="Applied after Save and a successful gateway sync. Choose which tools need review."
        component="h3"
      />
      <TextField
        select
        size="small"
        label="Require approval"
        value={form.require}
        disabled={disabled}
        onChange={(e) => change("require", e.target.value)}
      >
        <MenuItem value="none">No tools</MenuItem>
        <MenuItem value="listed">Selected tools</MenuItem>
        <MenuItem value="destructive">Destructive tools</MenuItem>
      </TextField>
      {form.require !== "none" && (
        <>
          {form.require === "listed" && (
            <TextField
              size="small"
              label="Approval tool patterns"
              helperText="One server/tool pattern per line, for example mock/delete_file."
              multiline
              minRows={2}
              value={form.tools}
              disabled={disabled}
              onChange={(e) => change("tools", e.target.value)}
            />
          )}
          <TextField
            select
            size="small"
            label="Approval validity"
            value={form.validity}
            disabled={disabled}
            onChange={(e) => change("validity", e.target.value)}
          >
            <MenuItem value="once">Once only</MenuItem>
            <MenuItem value="ttl">Reusable until expiry</MenuItem>
          </TextField>
          <TextField
            size="small"
            label="Approval expiry (seconds)"
            value={form.ttl}
            disabled={disabled}
            inputProps={{ inputMode: "numeric" }}
            onChange={(e) => change("ttl", e.target.value)}
          />
          <Typography typography="s2" color="text.secondary">
            The agent receives a pending refusal. An Owner or Admin decides,
            then the agent retries the same tool, arguments, key and run.
            Once-only approvals allow at most one dispatch, even across
            gateways.
          </Typography>
        </>
      )}
      {error && <Alert severity="error">{error}</Alert>}
    </Box>
  );
}
ApprovalPolicyFields.propTypes = {
  policy: PropTypes.object,
  disabled: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};
