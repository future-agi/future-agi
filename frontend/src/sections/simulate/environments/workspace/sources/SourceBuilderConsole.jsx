import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
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
  useEnvironmentSourceReview,
  useSourceCapabilities,
} from "src/api/simulate-environments/sourceImports";
import { draftsKey } from "src/api/simulate-environments/versions";
import BuilderConsole from "../../buildEnvironment/console/BuilderConsole";
import Iconify from "src/components/iconify";
import ShimmerText from "../../components/ShimmerText";
import useSourceAcceptedToast from "./useSourceAcceptedToast";
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
  const { data: activeReview } = useEnvironmentSourceReview(reviewId);
  const filesInReview =
    activeReview?.scope?.id === envId
      ? activeReview.preview?.sources || []
      : [];
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
          document_purposes: Object.fromEntries(
            batch.files.flatMap((file, index) =>
              file.name.toLowerCase().endsWith(".pdf")
                ? [
                    [
                      batch.ids[index],
                      batch.policyFiles.includes(file) ? "policy" : "reference",
                    ],
                  ]
                : [],
            ),
          ),

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
      names: files.map((f) => f.name),
      ids: [],
      policyFiles,
      key: harnessIdempotencyKey(),
      basis: capabilities.data,
    };
    prepare();
  };
  const close = () => setReviewId(null);
  const notifyAccepted = useSourceAcceptedToast();
  const refresh = (review, requestBody) => {
    notifyAccepted(review, requestBody);
    client.invalidateQueries({ queryKey: sourceSummaryKey(envId) });
    client.invalidateQueries({ queryKey: draftsKey(envId) });
  };
  const editor = (id) => (
    <Box
      component="section"
      aria-label="Proposed changes"
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}
    >
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        Proposed changes
      </Typography>
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
    </Box>
  );
  const status = state ? (
    <Stack spacing={1}>
      <Typography variant="body2">
        Attached: {state.names.join(", ")}
      </Typography>
      {state.busy ? (
        <ChatFileRows names={state.names} reading />
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
      reviewId === id && filesInReview.length
        ? {
            ...turn,
            text: [
              turn.text,
              "Attached: " +
                filesInReview
                  .map((s) => s.filename || "Source file")
                  .join(", "),
            ]
              .filter(Boolean)
              .join(" · "),
          }
        : turn,
      {
        id: turn.id + "-sources",
        role: "builder",
        steps: [
          {
            id: "sources-" + id,
            kind: "node",
            node: (
              <Stack spacing={1}>
                {reviewId === id && filesInReview.length > 0 && (
                  <ChatFileRows
                    names={filesInReview.map(
                      (s) => s.filename || "Source file",
                    )}
                    reading={activeReview.state === "reading"}
                  />
                )}
                {node}
              </Stack>
            ),
          },
        ],
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

function ChatFileRows({ names, reading }) {
  return (
    <Box
      component="section"
      aria-label="Attached files"
      sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
    >
      {names.map((name, index) => (
        <Stack
          key={index}
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 1.25,
            py: 1,
            "& + &": { borderTop: 1, borderColor: "divider" },
          }}
        >
          <Iconify icon="solar:file-text-linear" width={16} />
          <Typography
            variant="body2"
            sx={{ flex: 1, overflowWrap: "anywhere" }}
          >
            {name}
          </Typography>
          {reading ? (
            <ShimmerText>Reading…</ShimmerText>
          ) : (
            <Typography variant="caption" color="text.secondary">
              Read
            </Typography>
          )}
        </Stack>
      ))}
    </Box>
  );
}
ChatFileRows.propTypes = {
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
  reading: PropTypes.bool,
};
