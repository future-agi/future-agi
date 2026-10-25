import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { configWarningMessage } from "../hooks/useConfigPatch";

const DIFF_META_KEYS = new Set(["from_version", "to_version", "warnings"]);

// GET org-configs/{id}/diff/?compare_to=<active id>: what activating `target`
// changes, section by section, as {added, removed, changed}.
function useVersionDiff(targetId, activeId, enabled) {
  return useQuery({
    queryKey: ["agentcc-org-config-diff", targetId, activeId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.orgConfig.diff(targetId),
        {
          params: { compare_to: activeId },
        },
      );
      return data.result;
    },
    enabled: Boolean(enabled && targetId && activeId),
    staleTime: 60000,
    meta: { errorHandled: true },
  });
}

function changedSections(diff) {
  return Object.entries(diff || {}).filter(
    ([key, section]) =>
      !DIFF_META_KEYS.has(key) &&
      section &&
      ["added", "removed", "changed"].some(
        (kind) => Object.keys(section[kind] || {}).length > 0,
      ),
  );
}

// Whether activating flips managed_models.enabled, as {from, to}; null if not.
// An absent `enabled` means off.
function managedModelsFlip(section) {
  const { added = {}, removed = {}, changed = {} } = section || {};
  let from;
  let to;
  if ("enabled" in changed) {
    from = changed.enabled?.old;
    to = changed.enabled?.new;
  } else if ("enabled" in added) {
    to = added.enabled;
  } else if ("enabled" in removed) {
    from = removed.enabled;
  } else {
    return null;
  }
  return Boolean(from) === Boolean(to)
    ? null
    : { from: Boolean(from), to: Boolean(to) };
}

// The same flip read from the two versions themselves, for when the diff is
// not available. null when either version does not carry managed_models.
function managedModelsFlipBetween(active, target) {
  if (!active?.managed_models || !target?.managed_models) return null;
  const from = Boolean(active.managed_models.enabled);
  const to = Boolean(target.managed_models.enabled);
  return from === to ? null : { from, to };
}

function ManagedModelsWarning({ flip, version }) {
  return (
    <Alert severity="warning">
      {flip.to
        ? `Activating v${version} turns managed models on: requests can be billed to your managed credits.`
        : `Activating v${version} turns managed models off: requests that use managed models will fail until they are turned back on.`}
    </Alert>
  );
}

ManagedModelsWarning.propTypes = {
  flip: PropTypes.shape({ from: PropTypes.bool, to: PropTypes.bool })
    .isRequired,
  version: PropTypes.number,
};

const formatValue = (value) => JSON.stringify(value);

function SectionDiff({ name, section }) {
  const lines = [
    ...Object.entries(section.added || {}).map(
      ([key, value]) => `+ ${key}: ${formatValue(value)}`,
    ),
    ...Object.entries(section.removed || {}).map(
      ([key, value]) => `- ${key}: ${formatValue(value)}`,
    ),
    ...Object.entries(section.changed || {}).map(
      ([key, { old, new: next }]) =>
        `~ ${key}: ${formatValue(old)} → ${formatValue(next)}`,
    ),
  ];
  return (
    <Box>
      <Typography variant="subtitle2">{name}</Typography>
      <Box
        sx={{
          bgcolor: "action.hover",
          borderRadius: 1,
          p: 1,
          fontFamily: "monospace",
          fontSize: "0.75rem",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {lines.map((line) => (
          <div key={line}>{line}</div>
        ))}
      </Box>
    </Box>
  );
}

SectionDiff.propTypes = {
  name: PropTypes.string.isRequired,
  section: PropTypes.shape({
    added: PropTypes.object,
    removed: PropTypes.object,
    changed: PropTypes.object,
  }).isRequired,
};

function DiffBody({ target, active, diffQuery }) {
  if (!active) {
    return (
      <Alert severity="info">
        There is no active version to compare against.
      </Alert>
    );
  }
  if (diffQuery.isLoading) {
    return (
      <Stack alignItems="center" sx={{ py: 4 }}>
        <CircularProgress size={24} />
      </Stack>
    );
  }
  if (diffQuery.isError) {
    const flip = managedModelsFlipBetween(active, target);
    return (
      <Stack spacing={2}>
        {flip && <ManagedModelsWarning flip={flip} version={target.version} />}
        <Alert severity="error">
          Could not load the changes in v{target.version}. You can still
          activate it.
        </Alert>
      </Stack>
    );
  }

  const diff = diffQuery.data;
  const flip = diff?.managed_models
    ? managedModelsFlip(diff.managed_models)
    : managedModelsFlipBetween(active, target);
  const serverWarnings = (diff?.warnings || []).filter(
    (warning) => !(flip && warning?.code === "managed_models_change"),
  );
  const sections = changedSections(diff);

  return (
    <Stack spacing={2}>
      {flip && <ManagedModelsWarning flip={flip} version={target.version} />}
      {serverWarnings.map((warning, index) => (
        // Warnings carry no id, and two can share a message.
        <Alert key={index} severity="warning">
          {configWarningMessage(warning)}
        </Alert>
      ))}
      {sections.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          v{target.version} has the same settings as the active version.
        </Typography>
      ) : (
        sections.map(([name, section]) => (
          <SectionDiff key={name} name={name} section={section} />
        ))
      )}
    </Stack>
  );
}

const versionShape = PropTypes.shape({
  id: PropTypes.string.isRequired,
  version: PropTypes.number,
  managed_models: PropTypes.object,
});

DiffBody.propTypes = {
  target: versionShape.isRequired,
  active: versionShape,
  diffQuery: PropTypes.shape({
    isLoading: PropTypes.bool,
    isError: PropTypes.bool,
    data: PropTypes.object,
  }).isRequired,
};

// Shown before an older config version is activated: what it changes against
// the active version, with a warning when managed models would flip. A failed
// diff does not block the activation, because a rollback must stay possible
// while the diff endpoint is down; the warning then comes from the versions.
const ConfigDiffView = ({
  open,
  target,
  active,
  onConfirm,
  onCancel,
  isActivating,
}) => {
  const diffQuery = useVersionDiff(target?.id, active?.id, open);
  const isWaitingForDiff = Boolean(active) && diffQuery.isLoading;

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>
        Activate v{target?.version}
        {active ? ` (replaces v${active.version})` : ""}
      </DialogTitle>
      <DialogContent dividers>
        {target && (
          <DiffBody target={target} active={active} diffQuery={diffQuery} />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={isActivating}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={onConfirm}
          disabled={isActivating || isWaitingForDiff}
          startIcon={isActivating ? <CircularProgress size={16} /> : null}
        >
          {`Activate v${target?.version}`}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

ConfigDiffView.propTypes = {
  open: PropTypes.bool.isRequired,
  target: versionShape,
  active: versionShape,
  onConfirm: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  isActivating: PropTypes.bool,
};

export default ConfigDiffView;
