// Section 4 · side effects (R4-SPEC D21; lane Ra MUST 6): when the agent
// declares side-effect tools and the dataset has no sandbox Environment,
// `support_agent can call issue_refund, offer_replacement.` + `Run for
// real` · `Use tool stubs` · `Dry run, no tool calls`. The choice is the
// run's `safety.side_effects` (real | stubs | dry_run), the runner's
// `--side-effects` flag and serve(side_effects=).
// On a task suite with an Environment: `Tools run against this task
// suite's sandbox (…)`.
import React from "react";
import PropTypes from "prop-types";
import {
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import Term from "src/sections/common/ux/Term";
import { AmberLine } from "./parts";

const OPTIONS = [
  { id: "real", label: "Run for real" },
  {
    id: "stubs",
    label: "Use tool stubs",
    hint: "Write tools answer from stubs; nothing changes (--side-effects stubs)",
  },
  {
    id: "dry_run",
    label: "Dry run, no tool calls",
    hint: "Write tools are not called; the agent sees what it would do (--side-effects dry_run)",
  },
];

/** `stubbed` (older drafts) is `stubs` (C3). */
export const safetyOf = (v) => (v === "stubbed" ? "stubs" : v || "real");

export default function SafetyRow({ rule, value = "real", onChange }) {
  if (!rule?.state) return null;
  if (rule.state === "sandbox")
    return (
      <Stack
        direction="row"
        gap={0.75}
        alignItems="flex-start"
        data-testid="safety-row"
      >
        <Iconify
          icon="mdi:shield-check-outline"
          width={16}
          sx={{ color: "success.main", flexShrink: 0, mt: "2px" }}
        />
        <Typography typography="s2">{rule.line}</Typography>
      </Stack>
    );
  return (
    <Stack spacing={0.5} data-testid="safety-row">
      <AmberLine icon="mdi:flash-alert-outline">
        <Term k="sideEffects">{rule.line}</Term>
      </AmberLine>
      <RadioGroup
        row
        value={safetyOf(value)}
        onChange={(e) => onChange?.(e.target.value)}
        sx={{ pl: 1, columnGap: 1 }}
      >
        {OPTIONS.map((o) => (
          <FormControlLabel
            key={o.id}
            value={o.id}
            control={<Radio size="small" />}
            label={
              <Typography typography="s2" title={o.hint}>
                {o.label}
              </Typography>
            }
          />
        ))}
      </RadioGroup>
    </Stack>
  );
}

SafetyRow.propTypes = {
  rule: PropTypes.object,
  value: PropTypes.string,
  onChange: PropTypes.func,
};
