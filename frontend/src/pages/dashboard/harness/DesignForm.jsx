import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  createHarnessDesign,
  useHarnessReleases,
} from "src/api/harness/releases";
import { paths } from "src/routes/paths";
import useSimulationAccess from "src/sections/simulate/environments/hooks/useSimulationAccess";
import BuildFilesField from "src/sections/simulate/environments/buildDrawer/forms/shared/BuildFilesField";
import { useFileUploads } from "src/sections/simulate/environments/buildDrawer/forms/shared/useFileUploads";

import { errorMessage } from "./harnessShared";

const MODALITIES = [
  { value: "chat", label: "Chat" },
  { value: "voice", label: "Voice" },
];

// The design request (environment-releases.md §7.9): requirements, and optionally a root release
// to customize and the person's documents. The harness asks for anything else it needs.
export default function DesignForm({
  template = null,
  onCancel = null,
  onBusyChange,
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: releases = [] } = useHarnessReleases({ enabled: !template });
  // A customization is a new root: only root releases can be templates.
  const templates = useMemo(
    () =>
      releases.filter((release) => !release.parent && release.suites?.length),
    [releases],
  );
  const [chosen, setChosen] = useState(template);
  const [suiteId, setSuiteId] = useState(template?.suites?.[0]?.id || "");
  const [name, setName] = useState(
    template ? `${template.name} (customized)` : "",
  );
  const [requirements, setRequirements] = useState("");
  const [modality, setModality] = useState("chat");
  const uploads = useFileUploads();
  const { canEdit } = useSimulationAccess();
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    onBusyChange?.(submitting);
    return () => onBusyChange?.(false);
  }, [onBusyChange, submitting]);
  const [error, setError] = useState("");
  const request = useRef(null);
  const ready =
    canEdit &&
    Boolean(name.trim() && requirements.trim()) &&
    (!chosen || Boolean(suiteId)) &&
    !uploads.uploading.length &&
    !uploads.failed.length &&
    !submitting;

  const choose = (release) => {
    setChosen(release);
    setSuiteId(release?.suites?.[0]?.id || "");
  };

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const body = {
        name: name.trim(),
        requirements: requirements.trim(),
        modality,
        ...(chosen ? { template_suite_id: suiteId } : {}),
        document_ids: uploads.done.map(({ documentId }) => documentId),
      };
      const signature = JSON.stringify(body);
      if (request.current?.signature !== signature) {
        request.current = { signature, key: harnessIdempotencyKey() };
      }
      const created = await createHarnessDesign({
        ...body,
        idempotency_key: request.current.key,
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      navigate(
        paths.dashboard.simulate.environments.detail(created.job.job_id),
      );
    } catch (requestError) {
      setError(errorMessage(requestError));
      setSubmitting(false);
    }
  };

  return (
    <Stack spacing={2}>
      <TextField
        size="small"
        label="Name"
        placeholder="Returns desk"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <TextField
        size="small"
        multiline
        minRows={6}
        label="Requirements"
        placeholder={
          "Who your agent serves and over which channel, what it does for them, the systems it " +
          "acts on, the rules it must follow, and what failure looks like."
        }
        value={requirements}
        onChange={(event) => setRequirements(event.target.value)}
      />
      <TextField
        select
        size="small"
        label="Channel"
        value={modality}
        onChange={(event) => setModality(event.target.value)}
      >
        {MODALITIES.map((item) => (
          <MenuItem key={item.value} value={item.value}>
            {item.label}
          </MenuItem>
        ))}
      </TextField>
      {template ? (
        <Typography variant="body2">
          Starting from <strong>{template.name}</strong>: the result is a new
          environment of yours; the template stays as it is.
        </Typography>
      ) : (
        <Autocomplete
          size="small"
          options={templates}
          value={chosen}
          onChange={(_event, value) => choose(value)}
          getOptionLabel={(release) => release?.name || ""}
          isOptionEqualToValue={(option, value) => option.id === value.id}
          renderInput={(params) => (
            <TextField {...params} label="Start from a template (optional)" />
          )}
        />
      )}
      {chosen && (chosen.suites || []).length > 1 && (
        <TextField
          select
          size="small"
          label="Scenario suite"
          value={suiteId}
          onChange={(event) => setSuiteId(event.target.value)}
        >
          {chosen.suites.map((suite) => (
            <MenuItem key={suite.id} value={suite.id}>
              {suite.name} · {suite.scenario_count} scenarios
            </MenuItem>
          ))}
        </TextField>
      )}
      {canEdit && <BuildFilesField uploads={uploads} />}
      <Typography variant="caption" color="text.secondary">
        The harness asks what it still needs, one question at a time, and
        proposes the design for your approval before it builds anything. The
        sealed environment is yours: use it with your agent, run our agent on
        it, or customize it again.
      </Typography>
      {error && (
        <Alert severity="error" variant="outlined">
          {error}
        </Alert>
      )}
      <Stack direction="row" spacing={1} justifyContent="flex-end">
        {onCancel && (
          <Button color="inherit" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
        )}
        <Button
          variant="contained"
          disabled={!ready}
          onClick={submit}
          startIcon={
            submitting ? <CircularProgress size={14} color="inherit" /> : null
          }
        >
          {template || chosen ? "Customize" : "Design environment"}
        </Button>
      </Stack>
    </Stack>
  );
}

DesignForm.propTypes = {
  template: PropTypes.object,
  onCancel: PropTypes.func,
  onBusyChange: PropTypes.func,
};

export function DesignDialog({ open, onClose, template }) {
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Customize {template?.name}</DialogTitle>
      <DialogContent>
        <Box sx={{ pt: 1 }}>
          <DesignForm template={template} onCancel={onClose} />
        </Box>
      </DialogContent>
    </Dialog>
  );
}

DesignDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  template: PropTypes.object,
};
