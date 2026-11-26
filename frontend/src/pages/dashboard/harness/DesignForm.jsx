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
  FormControlLabel,
  Radio,
  RadioGroup,
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
import BuildSourceFields from "src/sections/simulate/environments/buildDrawer/forms/shared/BuildSourceFields";
import useBuildSources from "src/sections/simulate/environments/buildDrawer/forms/shared/useBuildSources";

import DescribeFlowCard from "src/sections/simulate/environments/buildDrawer/starts/DescribeFlowCard";
import { BUILD_FILES_ACCEPT } from "src/sections/simulate/environments/buildDrawer/forms/shared/buildFiles";
import { suggestedNameOf } from "src/sections/simulate/environments/design/design";
import { errorMessage } from "./harnessShared";

const MODALITIES = [
  { value: "chat", label: "Chat" },
  { value: "voice", label: "Voice" },
];

// The design request (environment-releases.md §7.9): requirements, and optionally a root release
// to customize and the person's documents. The harness asks for anything else it needs.
export default function DesignForm({
  scratch = false,
  template = null,
  onCancel = null,
  onBusyChange,
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: releases = [] } = useHarnessReleases({
    enabled: !template && !scratch,
  });
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
  const sources = useBuildSources();
  const [privacy, setPrivacy] = useState("synthetic_replace");
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
    Boolean((scratch || name.trim()) && requirements.trim()) &&
    (!chosen || Boolean(suiteId)) &&
    !sources.blocked &&
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
        name:
          name.trim() ||
          suggestedNameOf(requirements, sources.uploads.done) ||
          "Untitled environment",
        ...(scratch && sources.selection
          ? { personal_data_mode: privacy }
          : {}),
        requirements: requirements.trim(),
        modality,
        ...(chosen ? { template_suite_id: suiteId } : {}),
        ...(sources.selection ? { source_selection: sources.selection } : {}),
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

  if (scratch) {
    return (
      <Stack spacing={2}>
        <Typography variant="body2" color="text.secondary">
          No agent yet? Fine. Connect it later.
        </Typography>
        <TextField
          size="small"
          disabled={!canEdit || submitting}
          placeholder="e.g. Support desk sandbox"
          label="Environment name (optional)"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <DescribeFlowCard
          prompt="Describe the flow to build"
          description="Describe the trigger, tools and hand-offs. Attach policies, records or notes for context."
          placeholder="A customer asks for a refund. The agent checks the order and escalates purchases over $500."
          chips={[
            "The agent gets triggered when…",
            "It reaches for these tools…",
            "It's done when…",
            "It escalates if…",
          ]}
          value={requirements}
          onChange={setRequirements}
          files={sources.uploads.files}
          preparing={submitting}
          filesFooter={
            <>
              {canEdit && (
                <BuildSourceFields
                  sources={sources}
                  disabled={submitting}
                  showUpload={false}
                />
              )}
              {sources.selection && (
                <Stack>
                  <Typography id="scratch-personal-data">
                    Personal data in the sources
                  </Typography>
                  <RadioGroup
                    row
                    aria-labelledby="scratch-personal-data"
                    value={privacy}
                    onChange={(event) => setPrivacy(event.target.value)}
                  >
                    <FormControlLabel
                      value="synthetic_replace"
                      control={<Radio disabled={!canEdit || submitting} />}
                      label="Replace with stand-ins"
                    />
                    <FormControlLabel
                      value="none"
                      control={<Radio disabled={!canEdit || submitting} />}
                      label="There’s no personal data"
                    />
                  </RadioGroup>
                </Stack>
              )}
            </>
          }
          onAdd={sources.uploads.add}
          onRetry={sources.uploads.retry}
          onRemove={sources.uploads.remove}
          accept={BUILD_FILES_ACCEPT}
          disabled={!canEdit || submitting}
          canSubmit={ready}
          onNext={submit}
          submitLabel={
            submitting ? "Starting the builder…" : "Build environment"
          }
        />
        {error && <Alert severity="error">{error}</Alert>}
      </Stack>
    );
  }

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
      {canEdit && <BuildSourceFields sources={sources} disabled={submitting} />}
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
  scratch: PropTypes.bool,
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
