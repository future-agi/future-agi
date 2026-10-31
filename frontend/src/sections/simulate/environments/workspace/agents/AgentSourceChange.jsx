import PropTypes from "prop-types";
import { useEffect, useReducer, useRef, useState } from "react";
import {
  Box,
  Button,
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  STUCK_AFTER_MS,
  checkRunning,
  lastCheckStart,
  useCreateAgentVersion,
} from "src/api/simulate-environments/versions";
import { simulateApiHarnessEnvironmentsVersionsCreateAgentVersionBodyPromptMax as PROMPT_MAX_CHARS } from "src/generated/api-contracts/api.zod";
import {
  prepareSourceFolder,
  uploadErrorText,
  uploadSourceFolder,
} from "src/pages/dashboard/harness/sourceUpload";
import FolderInput from "../../components/FolderInput";
import { AGENT_VERSIONS_COPY } from "../workspace.constants";
import { refusalText } from "../evals/refusalText";

const COPY = AGENT_VERSIONS_COPY.change;

const errorText = (error) =>
  COPY.errors[error?.error] || refusalText(error, COPY.error);
// A ref that is a commit never moves, so it isn't watched.
const PINNED_REF = /^[0-9a-f]{40}$/i;

function Note({ children, color = "text.secondary" }) {
  return (
    <Typography sx={{ typography: "s3", color, mt: 0.5 }}>
      {children}
    </Typography>
  );
}
Note.propTypes = { children: PropTypes.node, color: PropTypes.string };

// Says why an action waits while another change's check runs.
function Waiting({ checking, children }) {
  return (
    <Tooltip arrow title={checking ? COPY.checkRunning : ""}>
      <Box component="span" sx={{ display: "inline-flex" }}>
        {children}
      </Box>
    </Tooltip>
  );
}
Waiting.propTypes = { checking: PropTypes.bool, children: PropTypes.node };

// The prompt (and a phone agent's number) of the default version, edited in
// place; saving makes the agent's next version.
function PromptEditor({ source, prompt, pending, held, onSave, onCancel }) {
  const phone = source.kind === "phone";
  const [text, setText] = useState(prompt || "");
  const [number, setNumber] = useState(source.phone_number || "");
  const trimmed = text.trim();
  const promptChanged = trimmed !== (prompt || "").trim();
  const numberChanged = phone && number.trim() !== (source.phone_number || "");
  const tooLong = text.length > PROMPT_MAX_CHARS;
  const canSave =
    !pending &&
    !held &&
    (promptChanged || numberChanged) &&
    Boolean(trimmed) &&
    !tooLong &&
    (!phone || Boolean(number.trim()));
  const save = (event) => {
    event.preventDefault();
    if (!canSave) return;
    onSave({
      ...(promptChanged ? { prompt: trimmed } : {}),
      ...(numberChanged
        ? { agent_payload: { config: { phone_number: number.trim() } } }
        : {}),
    });
  };
  return (
    <Box component="form" noValidate onSubmit={save} sx={{ mt: 1.5 }}>
      <TextField
        fullWidth
        multiline
        minRows={4}
        maxRows={14}
        size="small"
        label={source.kind === "provider" ? COPY.evalInstructions : COPY.prompt}
        value={text}
        onChange={(event) => setText(event.target.value)}
        error={tooLong}
        helperText={COPY.count(text.length, PROMPT_MAX_CHARS)}
      />
      {phone && (
        <TextField
          fullWidth
          size="small"
          label={COPY.phoneNumber}
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          helperText={COPY.phoneHint}
          sx={{ mt: 1.5 }}
        />
      )}
      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
        <Button
          type="submit"
          size="small"
          variant="contained"
          disabled={!canSave}
          sx={{ textTransform: "none" }}
        >
          {pending ? COPY.creating : COPY.savePrompt}
        </Button>
        <Button
          size="small"
          color="inherit"
          disabled={pending}
          onClick={onCancel}
          sx={{ textTransform: "none" }}
        >
          {COPY.cancel}
        </Button>
      </Stack>
    </Box>
  );
}
PromptEditor.propTypes = {
  source: PropTypes.object.isRequired,
  prompt: PropTypes.string,
  pending: PropTypes.bool,
  held: PropTypes.bool,
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

// What the default agent version is built from, and the change that source
// takes here: a GitHub source is watched, an uploaded folder is uploaded
// again, and a provider, phone or LiveKit agent's prompt is edited.
export default function AgentSourceChange({
  envId,
  defaultVersion,
  versions = [],
}) {
  const create = useCreateAgentVersion(envId);
  const [editing, setEditing] = useState(false);
  const [uploading, setUploading] = useState(null);
  const [error, setError] = useState(null);
  const [created, setCreated] = useState(null);
  const uploadSeq = useRef(0);
  // A running check turns stuck with no new data: render again when it does.
  const [, rerender] = useReducer((count) => count + 1, 0);
  const stuckAt = Math.min(
    ...versions
      .filter(checkRunning)
      .map((version) => lastCheckStart(version) + STUCK_AFTER_MS),
  );
  useEffect(() => {
    if (!Number.isFinite(stuckAt)) return undefined;
    const timer = setTimeout(rerender, Math.max(stuckAt - Date.now(), 0) + 1);
    return () => clearTimeout(timer);
  }, [stuckAt]);

  const source = defaultVersion?.source;
  if (!source || source.provider === "remote") return null;

  const submit = (body) => {
    setError(null);
    create.mutate(body, {
      onSuccess: (data) => {
        setCreated({ label: data?.agent_version?.label || null });
        setEditing(false);
      },
      onError: (refused) => setError(errorText(refused)),
    });
  };

  const upload = async (list) => {
    setError(null);
    setCreated(null);
    uploadSeq.current += 1;
    const seq = uploadSeq.current;
    let prepared;
    try {
      prepared = prepareSourceFolder(list);
    } catch (refused) {
      setError(refused.message);
      return;
    }
    setUploading(0);
    try {
      const result = await uploadSourceFolder(prepared, (percent) => {
        if (seq === uploadSeq.current) setUploading(percent);
      });
      if (seq !== uploadSeq.current) return;
      setUploading(null);
      submit({ archive_artifact_id: result?.source_id });
    } catch (refused) {
      if (seq !== uploadSeq.current) return;
      setUploading(null);
      setError(
        uploadErrorText(refused, (other) => refusalText(other, COPY.error)),
      );
    }
  };

  // One change at a time: another made while a check runs would build on the
  // old default and drop it.
  const checking = versions.some(checkRunning);
  const busy = create.isPending || uploading !== null || checking;
  const watched =
    source.visibility !== "private" && !PINNED_REF.test(source.ref || "");
  const prompted = ["phone", "livekit", "provider"].includes(source.kind);

  return (
    <Box
      data-testid="agent-source-change"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        p: 2,
      }}
    >
      {source.kind === "github" && (
        <>
          <Typography sx={{ typography: "s2" }}>
            {(watched ? COPY.watching : COPY.builtFrom)(
              source.repository,
              source.ref,
            )}
          </Typography>
          {source.visibility === "private" ? (
            <Note>{COPY.privateNotWatched}</Note>
          ) : (
            watched && <Note>{COPY.watchHint}</Note>
          )}
        </>
      )}

      {source.kind === "archive" && (
        <>
          <Waiting checking={checking}>
            <Button
              component="label"
              size="small"
              variant="outlined"
              color="inherit"
              disabled={busy}
              sx={{ textTransform: "none" }}
            >
              {create.isPending
                ? COPY.creating
                : uploading !== null
                  ? COPY.uploading(uploading)
                  : COPY.upload}
              <FolderInput onPick={upload} />
            </Button>
          </Waiting>
          {(uploading !== null || create.isPending) && (
            <LinearProgress
              variant={uploading > 0 ? "determinate" : "indeterminate"}
              value={uploading ?? 0}
              sx={{ mt: 1 }}
            />
          )}
        </>
      )}

      {prompted && !editing && (
        <>
          <Waiting checking={checking}>
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={busy}
              onClick={() => {
                setError(null);
                setCreated(null);
                setEditing(true);
              }}
              sx={{ textTransform: "none" }}
            >
              {source.kind === "provider"
                ? COPY.evalInstructions
                : COPY.editPrompt}
            </Button>
          </Waiting>
          {source.kind === "provider" && (
            <Note>{COPY.evalInstructionsHint(source.provider)}</Note>
          )}
        </>
      )}
      {prompted && editing && (
        <PromptEditor
          source={source}
          prompt={defaultVersion.prompt}
          pending={create.isPending}
          held={checking}
          onSave={submit}
          onCancel={() => {
            setError(null);
            setEditing(false);
          }}
        />
      )}

      {checking && (source.kind === "archive" || prompted) && (
        <Note>{COPY.checkRunning}</Note>
      )}
      {created && !editing && <Note>{COPY.created(created.label)}</Note>}
      {error && (
        <Typography
          role="alert"
          sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
        >
          {error}
        </Typography>
      )}
    </Box>
  );
}

AgentSourceChange.propTypes = {
  envId: PropTypes.string,
  versions: PropTypes.arrayOf(PropTypes.object),
  defaultVersion: PropTypes.shape({
    source: PropTypes.object,
    prompt: PropTypes.string,
  }),
};
