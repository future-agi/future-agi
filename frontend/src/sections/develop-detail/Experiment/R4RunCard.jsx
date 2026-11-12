// A run added from the run-source picker: logo, run name and
// letter, then one line saying what it is, where it runs and how it gets
// its results, with each unshipped part tagged. `Edit` reopens Level 3.
import React from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import { VOCAB } from "src/sections/common/vocab";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import { catalogEntry } from "src/sections/common/RunSources/catalog";
import { whereLine } from "src/sections/common/RunSources/WhereItRuns";
import { defaultRecipe } from "src/sections/common/RunSources/recipe/recipeModel";
import {
  PERMISSION_LABELS,
  mcpToolCount,
} from "src/sections/common/Runs/provenanceModel";
import { draftModelOf } from "./costEstimateModel";
import { runTriesOf } from "./estimateInputs";
import { fmtCost } from "src/sections/common/format/fmt";
import RunCardShell from "./RunCardShell";
import { capText } from "./reviewModel";
import { useExperimentForm } from "./ExperimentFormContext";
import { useCardValue } from "./cardWatch-g2";

/** `LangGraph · Trace + upload · Runs on this machine` (pure). */
export function r4CardLine(r4, { tries = 0, sandboxAllowed = false } = {}) {
  const e = catalogEntry(r4?.entryId);
  if (!e) return "";
  const bits = [e.name];
  bits.push(whereLine(r4.mode, e, r4.slot, { sandboxAllowed }));
  if (r4.machine?.name && r4.form === "connect") bits.push(r4.machine.name);
  // L5 G3: an endpoint, A2A or MCP run's own cap is the run's (no per-try
  // cap exists): `stops at $0.05 for the run of 24 tries (≈ $0.0021 a try)`.
  const cap = Number(r4.limits?.maxCostUsd);
  if (ENDPOINT_ENTRY_KINDS.has(e.kind) && Number.isFinite(cap) && cap > 0)
    bits.push(`stops at ${capText(cap, { runLevel: true, tries })}`);
  return bits.join(" · ");
}

const ENDPOINT_ENTRY_KINDS = new Set(["http", "a2a", "mcp_tool"]);
// The card's type in words (dataset integration FE-0: never "Agent harness" for an
// endpoint, A2A agent or MCP tool run).
const TYPE_LABEL = {
  code: VOCAB.YOUR_CODE ?? "Your code",
  harness: "Agent harness",
  http: "HTTP endpoint",
  a2a: "A2A agent",
  mcp_tool: "MCP tool",
};

const names = (list) =>
  list.length <= 2
    ? list.join(" and ")
    : `${list.slice(0, 2).join(", ")} and ${list.length - 2} more`;

/**
 * IT-R1 G1 P8: every run card says the same four things, in order:
 * `Model · tools/MCP · where it runs · per-try cap` (pure). Harness:
 * `claude-sonnet-4-5 · MCP acme-support (12 tools) · Runs on this machine ·
 * $1.00 per try`; your code: `your model · your tools · Runs on this
 * machine · stops at $5.00`.
 */
export function r4CardSummary(r4, { tries = 0, sandboxAllowed = false } = {}) {
  const e = catalogEntry(r4?.entryId);
  if (!e) return "";
  const where = whereLine(r4.mode, e, r4.slot, { sandboxAllowed });
  if (e.kind === "harness") {
    const rec = r4.slot?.harness ?? defaultRecipe(e, null);
    const mcp = (rec.mcp_servers ?? []).map((m) => {
      const n = mcpToolCount(m);
      return n ? `${m.name} (${n} tools)` : m.name;
    });
    const cap = Number(rec.max_cost_usd ?? rec.budget?.max_cost_usd);
    const runCap = Number(r4.limits?.maxCostUsd);
    // options-truth L3 G3: the model the run uses (S5), its permissions
    // and the run's own cap beside the per-try one.
    return [
      draftModelOf({ ...r4, kind: "harness" }) ?? rec.model ?? "default model",
      mcp.length ? `MCP ${mcp.join(", ")}` : "no MCP servers",
      PERMISSION_LABELS[rec.permissions] ?? null,
      where,
      Number.isFinite(cap) && cap > 0 ? `${fmtCost(cap)} per try` : null,
      Number.isFinite(runCap) && runCap > 0
        ? `stops at ${fmtCost(runCap)} per run of ${tries}`
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (e.kind === "code") {
    // IT-R5 G1 P4: a declared model / tools count like detected ones.
    // options-truth L3 G3 (S5): a changed model param is the model used.
    const used = draftModelOf(r4);
    const models = (r4.uses?.models ?? []).map((m) => m.name).filter(Boolean);
    if (!models.length && r4.declared?.model) models.push(r4.declared.model);
    if (used && !models.includes(used)) models.unshift(used);
    const tools =
      r4.uses?.tools?.length || (r4.declared?.tools ?? []).length || 0;
    const cap = Number(r4.limits?.maxCostUsd);
    // Nothing declared yet: the first trace tells.
    const unknown = !models.length && !tools;
    return [
      unknown
        ? "Model and tools are read from your first trace"
        : models.length
          ? names(models)
          : "your model",
      unknown
        ? null
        : tools
          ? `${tools} ${tools === 1 ? "tool" : "tools"}`
          : "your tools",
      where,
      // IT-R5 G1 P4: caps in one unit (per try, then the run's total).
      Number.isFinite(cap) && cap > 0
        ? `Stops at ${capText(cap, { perRun: true, tries })}`
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return r4CardLine(r4, { tries, sandboxAllowed });
}

export default function R4RunCard({
  control,
  fieldPrefix,
  letters = [],
  onRemove,
  onEdit,
  error,
  showErrors,
}) {
  const { allowed, isLoading, isError } = useFeatureAllowed(
    CAPABILITY.OMEGA_SANDBOXES,
  );
  const sandboxAllowed = allowed === true && !isLoading && !isError;
  const r4 = useCardValue(control, fieldPrefix, "r4") ?? {};
  const trials = useWatch({ control, name: "trials" });
  const { exampleCount } = useExperimentForm();
  // S5: the card's own tries (the value its run plays).
  const tries = (Number(exampleCount) || 0) * runTriesOf({ r4 }, trials);
  const name = useCardValue(control, fieldPrefix, "name");
  const entry = catalogEntry(r4.entryId);

  return (
    <RunCardShell
      icon={<SourceLogo entry={entry} size={18} />}
      letters={letters}
      title={name || entry?.name || "Run"}
      typeLabel={TYPE_LABEL[entry?.kind] ?? entry?.name ?? "Run"}
      onRemove={onRemove}
      error={showErrors ? error : null}
      // dataset integration FE-0: a kept code / harness run has no Edit (its setup
      // screen printed generated runner scripts); the run itself stays.
      actions={
        onEdit ? (
          <Button
            size="small"
            onClick={onEdit}
            sx={{ color: "text.secondary", typography: "s2", px: 1 }}
            data-testid="r4-card-edit"
          >
            Edit
          </Button>
        ) : null
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ minWidth: 0 }}
        data-testid="r4-run-card"
      >
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {r4CardSummary(r4, { tries, sandboxAllowed })}
        </Typography>
      </Stack>
    </RunCardShell>
  );
}

R4RunCard.propTypes = {
  control: PropTypes.object.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  letters: PropTypes.arrayOf(PropTypes.string),
  onRemove: PropTypes.func,
  onEdit: PropTypes.func,
  error: PropTypes.string,
  showErrors: PropTypes.bool,
};
