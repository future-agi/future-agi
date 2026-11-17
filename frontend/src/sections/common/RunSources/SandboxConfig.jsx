// Level 3 · Future AGI sandbox (options-truth L1 G2). Source (Image | Repo
// | Zip with a real drop zone), entry, setup, runtime, network (off |
// allowlist | open), resources and timeout; every field emits through
// onChange as `slot.sandbox` (C3), so the run starts itself with exactly
// this identity. The summary line is the run's `where`.
import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { VOCAB } from "src/sections/common/vocab";
import SecretsRow from "./SecretsRow";
import RecipeRow, { segmentedSx } from "./recipe/RecipeRow";
import { sandboxConfigOf, sandboxIssues, sandboxSummary } from "./sourceModel";
import {
  NETWORKS,
  RESOURCES,
  RUNTIMES,
  SANDBOX_DEFAULTS,
  SOURCES,
  hostsOf,
  resourcesLine,
} from "./snippets/sandbox";

/** `1.4 MB` (the zip drop zone). */
export function bytesLine(n) {
  const b = Number(n) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

const field = (
  value,
  onChange,
  { label, width = 260, mono = false, placeholder, testId },
) => (
  <TextField
    size="small"
    value={value ?? ""}
    placeholder={placeholder}
    onChange={(e) => onChange(e.target.value)}
    inputProps={{
      "aria-label": label,
      "data-testid": testId,
      style: mono ? { fontFamily: "monospace" } : undefined,
    }}
    sx={{ width, "& input": { py: 0.5, typography: "s2" } }}
  />
);

/** The editor's starting value for an entry and dataset (pure). */
export function sandboxStart(entry, dataset, value) {
  const ts =
    (entry?.languages ?? []).includes("typescript") &&
    !(entry?.languages ?? []).includes("python");
  return {
    ...SANDBOX_DEFAULTS,
    entry:
      entry?.kind === "harness"
        ? ""
        : entry?.agent?.spec ?? SANDBOX_DEFAULTS.entry,
    runtime: ts ? "node22" : "python3.12",
    setup: ts ? "npm ci" : SANDBOX_DEFAULTS.setup,
    image: dataset?.environment?.image ?? SANDBOX_DEFAULTS.image,
    ...(value ?? {}),
  };
}

/** The C3 sandbox value an editor state emits (pure). */
export function sandboxEmit(c, entry) {
  return sandboxConfigOf(
    {
      ...c,
      allowlist: c.network === "allowlist" ? hostsOf(c.allowlist) : [],
      resources: {
        cpu: c.cpu,
        memory_gb: c.memory_gb,
        timeout_s: c.timeout_s,
      },
    },
    entry,
  );
}

function ZipDrop({ zip, onZip }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const take = (file) => {
    if (!file) return;
    onZip({ name: file.name, size: file.size });
  };
  return (
    <Box
      data-testid="sandbox-zip-drop"
      onClick={() => input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer?.files?.[0]);
      }}
      sx={{
        border: "1px dashed",
        borderColor: over ? "primary.main" : "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1,
        cursor: "pointer",
        minWidth: 320,
      }}
    >
      <input
        ref={input}
        type="file"
        accept=".zip,application/zip"
        hidden
        data-testid="sandbox-zip-input"
        onChange={(e) => take(e.target.files?.[0])}
      />
      {zip ? (
        <Typography typography="s2" sx={{ fontFamily: "monospace" }}>
          {zip.name} · {bytesLine(zip.size)}
        </Typography>
      ) : (
        <Typography typography="s2" color="text.secondary">
          Drop a .zip of your project folder here, or click to choose one
        </Typography>
      )}
    </Box>
  );
}
ZipDrop.propTypes = { zip: PropTypes.object, onZip: PropTypes.func };

export default function SandboxConfig({
  entry,
  dataset,
  value,
  onChange,
  models,
  agentModels,
  showSecrets = true,
}) {
  const initial = value?.sandbox ?? null;
  const [c, setC] = useState(() =>
    sandboxStart(entry, dataset, initial ? editorOf(initial) : null),
  );
  const emit = (next) => onChange?.({ sandbox: sandboxEmit(next, entry) });
  const set = (patch) => {
    const next = { ...c, ...patch };
    setC(next);
    emit(next);
  };
  // the defaults are this run's settings too, before any edit
  const sent = useRef(false);
  useEffect(() => {
    if (sent.current || value?.sandbox) return;
    sent.current = true;
    emit(c);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const summary = sandboxSummary(sandboxEmit(c, entry));
  const issues = sandboxIssues(sandboxEmit(c, entry));
  return (
    <Stack spacing={0.75} data-testid="sandbox-config">
      <Stack direction="row" alignItems="center" gap={1}>
        <Iconify
          icon="mdi:cube-outline"
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {VOCAB.SANDBOX}
        </Typography>
      </Stack>
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ fontFamily: "monospace" }}
        data-testid="sandbox-summary"
      >
        {summary}
      </Typography>
      <RecipeRow label="Source" hint="Where the sandbox gets your code">
        <ToggleButtonGroup
          size="small"
          exclusive
          value={c.source}
          onChange={(_, v) => v && set({ source: v })}
          aria-label="Source"
          sx={segmentedSx}
        >
          {SOURCES.map((s) => (
            <ToggleButton
              key={s.id}
              value={s.id}
              data-testid={`sandbox-source-${s.id}`}
            >
              {s.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </RecipeRow>
      <RecipeRow label="">
        {c.source === "repo" ? (
          <>
            {field(c.repo, (v) => set({ repo: v }), {
              label: "Repository",
              width: 220,
              mono: true,
              testId: "sandbox-repo",
            })}
            {field(c.branch, (v) => set({ branch: v }), {
              label: "Branch",
              width: 110,
              mono: true,
              testId: "sandbox-branch",
            })}
          </>
        ) : c.source === "zip" ? (
          <Stack spacing={0.5}>
            <ZipDrop zip={c.zip} onZip={(zip) => set({ zip })} />
            {/* L3 G4 fix 9: Add waits for the zip (sourceModel.sandboxIssues) */}
            {issues.block ? (
              <Typography
                typography="s2"
                color="warning.dark"
                data-testid="sandbox-block"
              >
                {issues.block}
              </Typography>
            ) : null}
          </Stack>
        ) : (
          field(c.image, (v) => set({ image: v }), {
            label: "Container image",
            width: 340,
            mono: true,
            testId: "sandbox-image",
          })
        )}
      </RecipeRow>
      {entry?.kind === "harness" ? null : (
        <RecipeRow label="Entry" hint="The file and variable each row calls">
          {field(c.entry, (v) => set({ entry: v }), {
            label: "Entry",
            width: 220,
            mono: true,
          })}
        </RecipeRow>
      )}
      <RecipeRow label="Setup" hint="Runs once before the first row">
        {field(c.setup, (v) => set({ setup: v }), {
          label: "Setup command",
          width: 300,
          mono: true,
        })}
      </RecipeRow>
      <RecipeRow label="Runtime">
        <Select
          size="small"
          value={c.runtime}
          onChange={(e) => set({ runtime: e.target.value })}
          sx={{ height: 30, typography: "s2", minWidth: 140 }}
          inputProps={{ "aria-label": "Runtime" }}
        >
          {RUNTIMES.map((r) => (
            <MenuItem key={r.id} value={r.id} sx={{ typography: "s2" }}>
              {r.label}
            </MenuItem>
          ))}
        </Select>
        {issues.warn ? (
          <Typography
            typography="s2"
            color="warning.dark"
            data-testid="sandbox-runtime-warn"
          >
            {issues.warn}
          </Typography>
        ) : null}
      </RecipeRow>
      <RecipeRow
        label="Network"
        hint={
          c.network === "off"
            ? "Off: the agent reaches only the Future AGI gateway"
            : c.network === "open"
              ? "Open: the agent reaches any host"
              : "Allowlist: the gateway plus these hosts"
        }
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={c.network}
          onChange={(_, v) => v && set({ network: v })}
          aria-label="Network"
          sx={segmentedSx}
        >
          {NETWORKS.map((n) => (
            <ToggleButton
              key={n.id}
              value={n.id}
              data-testid={`sandbox-network-${n.id}`}
            >
              {n.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {c.network === "allowlist"
          ? field(
              Array.isArray(c.allowlist) ? c.allowlist.join(", ") : c.allowlist,
              (v) => set({ allowlist: v }),
              {
                label: "Allowed hosts",
                width: 240,
                mono: true,
                placeholder: "api.acme.ai, *.stripe.com",
                testId: "sandbox-allowlist",
              },
            )
          : null}
      </RecipeRow>
      <RecipeRow label="Resources">
        <Select
          size="small"
          value={`${c.cpu}|${c.memory_gb}`}
          onChange={(e) => {
            const [cpu, mem] = e.target.value.split("|").map(Number);
            set({ cpu, memory_gb: mem });
          }}
          sx={{ height: 30, typography: "s2", minWidth: 140 }}
          inputProps={{ "aria-label": "Resources" }}
          data-testid="sandbox-resources"
        >
          {RESOURCES.map(([cpu, mem]) => (
            <MenuItem
              key={cpu}
              value={`${cpu}|${mem}`}
              sx={{ typography: "s2" }}
            >
              {resourcesLine({ cpu, memory_gb: mem })}
            </MenuItem>
          ))}
        </Select>
        <TextField
          size="small"
          type="number"
          value={c.timeout_s}
          onChange={(e) => set({ timeout_s: Number(e.target.value) || 0 })}
          inputProps={{
            min: 60,
            step: 60,
            "aria-label": "Sandbox timeout (s)",
          }}
          sx={{ width: 110, "& input": { py: 0.5, typography: "s2" } }}
          InputProps={{
            endAdornment: (
              <Typography typography="s3" color="text.secondary">
                s
              </Typography>
            ),
          }}
        />
      </RecipeRow>
      {/* options-truth L2 G4 (C3): one key row per screen; a harness or
          voice config passes its own model and shows its own row. */}
      {showSecrets ? (
        <SecretsRow
          mode="sandbox"
          kind={entry?.kind ?? "code"}
          models={(models ?? []).map((m) => m?.name ?? m?.id ?? m)}
          agentModels={agentModels}
        />
      ) : null}
    </Stack>
  );
}

/** A C3 sandbox value back into the editor's flat state (pure). */
function editorOf(v) {
  return {
    ...v,
    cpu: v.resources?.cpu ?? v.cpu,
    memory_gb: v.resources?.memory_gb ?? v.memory_gb,
    timeout_s: v.resources?.timeout_s ?? v.timeout_s,
  };
}

SandboxConfig.propTypes = {
  entry: PropTypes.object,
  mode: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func,
  dataset: PropTypes.object,
  ids: PropTypes.shape({
    datasetId: PropTypes.string,
    runLabel: PropTypes.string,
    token: PropTypes.string,
  }),
  /** The run's models: the key row asks for their keys (none → none). */
  models: PropTypes.array,
  /** False when the embedding config renders the one key row itself. */
  showSecrets: PropTypes.bool,
  /** The models the code's agent calls (its sample), for the key row. */
  agentModels: PropTypes.array,
  onEstimate: PropTypes.func,
  onTryResult: PropTypes.func,
  /** Additive (Ra): the Rerun dialog renders the recipe compactly. */
  compact: PropTypes.bool,
};
