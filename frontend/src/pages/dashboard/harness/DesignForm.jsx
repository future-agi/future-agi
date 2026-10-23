import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
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

import Iconify from "src/components/iconify";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  createHarnessDesign,
  uploadHarnessDesignDocument,
  useHarnessReleases,
} from "src/api/harness/releases";
import { paths } from "src/routes/paths";

import { errorMessage } from "./harnessShared";

const DOCUMENT_TYPES =
  ".pdf,.docx,.xlsx,.csv,.tsv,.json,.jsonl,.txt,.md";
const DOCUMENT_MAX_BYTES = 25 * 1024 * 1024;

const MODALITIES = [
  { value: "chat", label: "Chat" },
  { value: "voice", label: "Voice" },
];

// The design request (environment-releases.md §7.9): requirements, and optionally a root release
// to customize and the person's documents. The harness asks for anything else it needs.
export default function DesignForm({ template = null, onCancel = null }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileInput = useRef(null);
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
  // Each document is uploaded when it is picked; the design lists the ones that arrived.
  const [documents, setDocuments] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey] = useState(harnessIdempotencyKey);
  const uploading = documents.some((one) => one.state === "uploading");
  const ready =
    Boolean(name.trim() && requirements.trim()) &&
    (!chosen || Boolean(suiteId)) &&
    !uploading &&
    !submitting;

  const choose = (release) => {
    setChosen(release);
    setSuiteId(release?.suites?.[0]?.id || "");
  };

  const pick = (files) => {
    Array.from(files || []).forEach((file) => {
      const key = `${file.name}-${file.size}-${file.lastModified}`;
      if (file.size > DOCUMENT_MAX_BYTES) {
        setDocuments((all) => [
          ...all,
          { key, name: file.name, state: "error", error: "Larger than 25 MB" },
        ]);
        return;
      }
      setDocuments((all) => [
        ...all,
        { key, name: file.name, state: "uploading" },
      ]);
      uploadHarnessDesignDocument(file)
        .then((stored) =>
          setDocuments((all) =>
            all.map((one) =>
              one.key === key
                ? { ...one, state: "uploaded", id: stored.id }
                : one,
            ),
          ),
        )
        .catch((requestError) =>
          setDocuments((all) =>
            all.map((one) =>
              one.key === key
                ? { ...one, state: "error", error: errorMessage(requestError) }
                : one,
            ),
          ),
        );
    });
  };

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const created = await createHarnessDesign({
        name: name.trim(),
        requirements: requirements.trim(),
        modality,
        ...(chosen ? { template_suite_id: suiteId } : {}),
        document_ids: documents
          .filter((one) => one.state === "uploaded")
          .map((one) => one.id),
        idempotency_key: idempotencyKey,
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      navigate(paths.dashboard.simulate.harness.detail(created.job.job_id));
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
      <Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            startIcon={<Iconify icon="solar:paperclip-linear" width={16} />}
            onClick={() => fileInput.current?.click()}
          >
            Add documents
          </Button>
          <Typography variant="caption" color="text.secondary">
            Product lists, schedules, policies or FAQs: PDF, Word, Excel, CSV,
            JSON or text, up to 25 MB each.
          </Typography>
        </Stack>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept={DOCUMENT_TYPES}
          aria-label="Documents"
          onChange={(event) => {
            pick(event.target.files);
            event.target.value = "";
          }}
        />
        {documents.length > 0 && (
          <Stack
            direction="row"
            spacing={0.75}
            useFlexGap
            flexWrap="wrap"
            sx={{ mt: 1 }}
          >
            {documents.map((one) => (
              <Chip
                key={one.key}
                size="small"
                color={one.state === "error" ? "error" : "default"}
                variant={one.state === "uploaded" ? "filled" : "outlined"}
                label={
                  one.state === "error" ? `${one.name}: ${one.error}` : one.name
                }
                icon={
                  one.state === "uploading" ? (
                    <CircularProgress size={12} />
                  ) : undefined
                }
                onDelete={() =>
                  setDocuments((all) =>
                    all.filter((item) => item.key !== one.key),
                  )
                }
              />
            ))}
          </Stack>
        )}
      </Box>
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
