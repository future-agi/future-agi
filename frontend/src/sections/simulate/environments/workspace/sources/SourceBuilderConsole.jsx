import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Alert, Button, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import {
  harnessIdempotencyKey,
  sendHarnessConversationMessage,
} from "src/api/harness/harness";
import { useUploadDesignDocument } from "src/api/simulate-environments/designDocuments";
import {
  sourceSummaryKey,
  useActiveEnvironmentSourceReview,
  useCreateSourceReview,
  useSourceCapabilities,
} from "src/api/simulate-environments/sourceImports";
import { draftsKey } from "src/api/simulate-environments/versions";
import BuilderConsole from "../../buildEnvironment/console/BuilderConsole";
import SourceReviewPanel from "./SourceReviewPanel";
import { overUploadLimit } from "./sources.constants";

export default function SourceBuilderConsole({
  envId,
  turns = [],
  onSend,
  ...props
}) {
  const upload = useUploadDesignDocument();
  const capabilities = useSourceCapabilities(envId);
  const create = useCreateSourceReview();
  const { reviewId, setReviewId } = useActiveEnvironmentSourceReview(envId);
  const client = useQueryClient();
  const pending = useRef(null);
  const [state, setState] = useState(null);

  const prepare = async () => {
    const batch = pending.current;
    if (!batch || batch.running) return;
    batch.running = true;
    setState({ busy: true, names: batch.names });
    try {
      const limit = overUploadLimit(batch.files, batch.basis.upload);
      if (limit) throw new Error(limit);
      for (const [index, file] of batch.files.entries()) {
        if (!batch.ids[index]) batch.ids[index] = (await upload(file)).id;
      }
      if (!batch.reviewId) {
        const review = await create.mutateAsync({
          scope: {
            kind: "environment",
            id: envId,
            environment_version_id: batch.basis.environment_version_id,
            expected_draft: batch.basis.draft,
          },
          document_ids: batch.ids,
          document_purposes: Object.fromEntries(batch.files.flatMap((file, index) =>
            file.name.toLowerCase().endsWith(".pdf") ? [[batch.ids[index], batch.policyFiles.includes(file) ? "policy" : "reference"]] : [])),

          idempotency_key: batch.key,
        });
        batch.reviewId = review.id;
      }
      await sendHarnessConversationMessage(envId, {
        content: batch.text || "Review the attached files.",
        kind: "user_message",
        source_review_id: batch.reviewId,
        client_request_id: batch.key,
      });
      setReviewId(batch.reviewId);
      client.invalidateQueries({ queryKey: ["harness-job", envId] });
      pending.current = null;
      setState(null);
    } catch (err) {
      batch.running = false;
      setState({
        error: err.detail || err.message || "Could not prepare these files.",
        names: batch.names,
      });
    }
  };

  const send = (text, files = [], policyFiles = []) => {
    if (!files.length) return onSend(text);
    if (pending.current || reviewId || !capabilities.data) return;
    pending.current = {
      text,
      files,
      names: files.map((f) => f.name).join(", "),
      ids: [],
      policyFiles,
      key: harnessIdempotencyKey(),
      basis: capabilities.data,
    };
    prepare();
  };
  const close = () => setReviewId(null);
  const refresh = () => {
    client.invalidateQueries({ queryKey: sourceSummaryKey(envId) });
    client.invalidateQueries({ queryKey: draftsKey(envId) });
  };
  const editor = (id) => (
    <SourceReviewPanel
      reviewId={id}
      ownerId={envId}
      canEdit={capabilities.isSuccess}
      environmentBasis={capabilities.data}
      acceptLabel="Add to draft"
      onAccepted={refresh}
      onCancelled={close}
      onDismiss={close}
    />
  );
  const status = state ? (
    <Stack spacing={1}>
      <Typography>{state.names}</Typography>
      {state.busy ? (
        <Typography>Preparing files for review…</Typography>
      ) : (
        <Alert
          severity="error"
          action={
            <Stack direction="row">
              <Button onClick={prepare}>Retry</Button>
              <Button
                onClick={() => {
                  pending.current = null;
                  setState(null);
                }}
              >
                Dismiss
              </Button>
            </Stack>
          }
        >
          {state.error}
        </Alert>
      )}
    </Stack>
  ) : null;
  const history = turns.flatMap((turn) => {
    const id = turn.sourceReviewId;
    if (!id) return [turn];
    const node =
      reviewId === id ? (
        editor(id)
      ) : (
        <Button disabled={Boolean(reviewId)} onClick={() => setReviewId(id)}>
          View attached source review
        </Button>
      );
    return [
      turn,
      {
        id: turn.id + "-sources",
        role: "builder",
        steps: [{ id: "sources-" + id, kind: "node", node }],
      },
    ];
  });
  if (status)
    history.push({
      id: "upload-status",
      role: "builder",
      steps: [{ id: "pending-files", kind: "node", node: status }],
    });
  return (
    <BuilderConsole
      {...props}
      canChooseFilePurpose
      canAttach={Boolean(capabilities.data && !reviewId && !state)}
      onSend={send}
      turns={history}
    />
  );
}
SourceBuilderConsole.propTypes = {
  envId: PropTypes.string.isRequired,
  turns: PropTypes.array,
  onSend: PropTypes.func.isRequired,
};
