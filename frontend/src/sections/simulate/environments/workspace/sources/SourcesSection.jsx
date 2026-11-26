import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import useSourceAcceptedToast from "./useSourceAcceptedToast";
import Iconify from "src/components/iconify";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  sourceSummaryKey,
  sourceGenerationPending,
  useEnvironmentSourceGeneration,
  useEnvironmentSourceReview,
  useActiveEnvironmentSourceReview,
  useCreateSourceReview,
  useEnvironmentSources,
  useSourceCapabilities,
} from "src/api/simulate-environments/sourceImports";
import { draftsKey } from "src/api/simulate-environments/versions";
import SectionCard from "../../components/SectionCard";
import { BUILD_FILES_ACCEPT } from "../../buildDrawer/forms/shared/buildFiles";
import useBuildSources from "../../buildDrawer/forms/shared/useBuildSources";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import SourceReviewPanel from "./SourceReviewPanel";
import { ProposalDrawerFrame } from "./ProposalsDrawer";
import AddSourceMenu from "./AddSourceMenu";
import KnowledgeBasePicker from "./KnowledgeBasePicker";
import SourcesTable from "./SourcesTable";
import { overUploadLimit, SOURCES_COPY } from "./sources.constants";

export default function SourcesSection({ env, locked }) {
  const location = useLocation();
  const navigate = useNavigate();

  const queryClient = useQueryClient();
  const { canEdit } = useSimulationAccess();
  const writable = canEdit && !locked;
  const sources = useBuildSources();
  const { reviewId, setReviewId } = useActiveEnvironmentSourceReview(env.id);
  const [reviewOpen, setReviewOpen] = useState(Boolean(reviewId));
  const [selecting, setSelecting] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const [kbOpen, setKbOpen] = useState(false);
  const [limitError, setLimitError] = useState("");
  const request = useRef(null);
  const consumedSelection = useRef(null);
  const create = useCreateSourceReview();
  const summary = useEnvironmentSources(env.id);
  const capabilities = useSourceCapabilities(env.id);
  const { data: activeReview } = useEnvironmentSourceReview(reviewId);
  const acceptedReview =
    activeReview?.state === "accepted" && activeReview.scope.id === env.id
      ? activeReview
      : null;
  const rows = summary.data?.sources || [];
  const generationId = acceptedReview?.id || summary.data?.generation_review_id;
  const initialGeneration =
    acceptedReview?.scenario_generation || summary.data?.scenario_generation;
  const { data: generation } = useEnvironmentSourceGeneration(
    generationId,
    initialGeneration,
  );
  const pendingSources =
    sourceGenerationPending(generation) || generation?.state === "failed"
      ? (acceptedReview?.preview?.sources || []).filter(
          (item) => !rows.some((row) => row.source_id === item.pin.source_id),
        )
      : [];
  const sourceRows = [
    ...rows,
    ...pendingSources.map((item) => ({
      source_id: item.pin.source_id,
      filename: item.filename,
      purpose: item.pin.role,
      review_id: acceptedReview.id,
      visibility: item.visibility,
      unsaved: true,
    })),
  ];
  const wasGenerating = useRef(false);
  useEffect(() => {
    if (
      wasGenerating.current &&
      generation &&
      !sourceGenerationPending(generation)
    ) {
      queryClient.invalidateQueries({ queryKey: sourceSummaryKey(env.id) });
      queryClient.invalidateQueries({ queryKey: draftsKey(env.id) });
    }
    wasGenerating.current = sourceGenerationPending(generation);
  }, [env.id, generation, queryClient]);

  function begin() {
    request.current = null;
    create.reset();
    setLimitError("");
    setReviewOpen(true);
    setSelecting(true);
  }

  useEffect(() => {
    const selection = location.state?.environmentSourceSelection;
    if (
      !writable ||
      reviewId ||
      !selection ||
      consumedSelection.current === location.key
    )
      return;
    consumedSelection.current = location.key;
    sources.addKnowledgeSelection(
      { kind: "knowledge_base", ...selection },
      "Selected Knowledge Base",
    );
    begin();
    navigate(location.pathname + location.search, { replace: true, state: {} });
  });

  useEffect(() => {
    if (
      !selecting ||
      !writable ||
      reviewId ||
      sources.blocked ||
      !sources.selection ||
      !capabilities.data ||
      create.isPending ||
      create.isError
    )
      return;
    const body = {
      scope: {
        kind: "environment",
        id: env.id,
        environment_version_id: capabilities.data.environment_version_id,
        expected_draft: capabilities.data.draft,
      },
      ...sources.selection,
    };
    const signature = JSON.stringify(body);
    if (request.current?.signature !== signature)
      request.current = {
        signature,
        key: harnessIdempotencyKey(),
        sent: false,
      };
    if (request.current.sent) return;
    request.current.sent = true;
    create.mutate(
      { ...body, idempotency_key: request.current.key },
      {
        onSuccess: (review) => {
          setReviewId(review.id);
          setSelecting(false);
          sources.clear();
        },
      },
    );
  });

  function closeReview() {
    setReviewId(null);
    setReviewOpen(false);
    setSelecting(false);
    sources.clear();
  }

  const notifyAccepted = useSourceAcceptedToast(() => setReviewOpen(false));
  function accepted(review, requestBody) {
    queryClient.invalidateQueries({ queryKey: sourceSummaryKey(env.id) });
    queryClient.invalidateQueries({ queryKey: draftsKey(env.id) });
    notifyAccepted(review, requestBody);
  }

  const error =
    limitError || summary.error?.message || capabilities.error?.message;
  const unsaved = sourceRows.some((source) => source.unsaved);
  const draftGeneration = unsaved && generation;
  const resumable = draftGeneration && generationId;
  const pendingReview = reviewId || selecting;
  const generationHeld =
    draftGeneration &&
    ["failed", "queued", "running", "stale"].includes(draftGeneration.state);
  return (
    <SectionCard
      id="environment-sources"
      sx={{ mb: 2 }}
      title={SOURCES_COPY.title(sourceRows.length)}
      subtitle={
        !sourceRows.length
          ? "Attach files in builder chat or add them here."
          : undefined
      }
      action={
        <Stack direction="row" spacing={1}>
          {(pendingReview || resumable) && (
            <Button
              size="small"
              onClick={() => {
                if (!pendingReview) setReviewId(resumable);
                setReviewOpen(true);
              }}
            >
              Review sources
            </Button>
          )}
          {writable && (
            <Button
              size="small"
              variant="outlined"
              disabled={Boolean(pendingReview || unsaved) || !capabilities.data}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              onClick={(event) => setAnchor(event.currentTarget)}
            >
              Add source
            </Button>
          )}
        </Stack>
      }
    >
      {error && (
        <Alert severity="error" sx={{ mx: 1.5, mt: 1.5 }}>
          {error}
        </Alert>
      )}
      {generationHeld && (
        <Alert
          severity={draftGeneration.state === "failed" ? "error" : "info"}
          sx={{ mx: 1.5, mt: 1.5 }}
        >
          {draftGeneration.state === "failed"
            ? "Scenario generation failed. Open the source review to retry."
            : draftGeneration.state === "stale"
              ? "This source generation is out of date. Prepare the sources again."
              : "Scenario generation is in progress."}
        </Alert>
      )}
      {writable && (unsaved || pendingReview) && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ px: 1.5, py: 1 }}
        >
          {unsaved
            ? "Save or discard the current draft before adding another source."
            : "Finish the current source review before adding another source."}
        </Typography>
      )}
      {summary.isLoading && (
        <Typography sx={{ p: 1.5 }}>Loading sources…</Typography>
      )}
      <SourcesTable
        envId={env.id}
        sources={sourceRows}
        generation={generation}
      />
      {writable && (
        <AddSourceMenu
          anchorEl={anchor}
          onClose={() => setAnchor(null)}
          limits={
            capabilities.data?.upload && {
              ...capabilities.data.upload,
              formats: BUILD_FILES_ACCEPT.split(",").map((extension) =>
                extension.slice(1),
              ),
            }
          }
          onAddFiles={(files) => {
            const errorText = overUploadLimit(files, capabilities.data?.upload);
            if (errorText) {
              setLimitError(errorText);
              return;
            }
            begin();
            sources.uploads.add(files);
          }}
          onAddKb={
            capabilities.data?.knowledge_base?.enabled
              ? () => setKbOpen(true)
              : undefined
          }
        />
      )}
      {kbOpen && (
        <KnowledgeBasePicker
          envId={env.id}
          open
          onClose={() => setKbOpen(false)}
          onPick={(selection) => {
            setKbOpen(false);
            begin();
            sources.addKnowledge(selection);
          }}
        />
      )}
      <ProposalDrawerFrame
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        subtitle="Review the changes before adding them to this environment."
      >
        {reviewId ? (
          <Box
            sx={{
              px: 2.5,
              pt: 1.5,
              minHeight: "100%",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <SourceReviewPanel
              readingNames={[
                ...sources.uploads.files.map((file) => file.name),
                ...sources.knowledge.map((item) => item.label),
              ]}
              reviewId={reviewId}
              ownerId={env.id}
              canEdit={writable && capabilities.isSuccess}
              environmentBasis={capabilities.data}
              acceptLabel="Add to draft"
              onAccepted={accepted}
              onCancelled={closeReview}
              onDismiss={closeReview}
            />
          </Box>
        ) : (
          <Stack spacing={1} sx={{ p: 2 }}>
            {sources.uploads.files.map((file) => (
              <Stack
                key={file.id}
                direction="row"
                spacing={1}
                alignItems="center"
              >
                <Typography role="status" color="text.secondary">
                  {file.status === "failed"
                    ? `Upload failed: ${file.name}`
                    : file.status === "uploading"
                      ? `Uploading ${file.name}…`
                      : `Reading ${file.name}…`}
                </Typography>
                {file.status === "failed" && (
                  <>
                    <Button onClick={() => sources.uploads.retry(file.id)}>
                      Retry
                    </Button>
                    <Button onClick={() => sources.uploads.remove(file.id)}>
                      Remove
                    </Button>
                  </>
                )}
              </Stack>
            ))}
            {sources.knowledge.map((item) => (
              <Typography role="status" key={item.selection.kb_id}>
                Reading {item.label}…
              </Typography>
            ))}
            {create.isError ? (
              <Alert
                severity="error"
                action={
                  <Button
                    onClick={() => {
                      request.current.sent = false;
                      create.reset();
                    }}
                  >
                    Try again
                  </Button>
                }
              >
                {create.error?.message || "Could not prepare sources."}
              </Alert>
            ) : (
              !sources.blocked &&
              !sources.uploads.files.length &&
              !sources.knowledge.length && (
                <Typography role="status">
                  Select files to prepare a review.
                </Typography>
              )
            )}
            {!create.isPending && <Button onClick={closeReview}>Cancel</Button>}
          </Stack>
        )}
      </ProposalDrawerFrame>
    </SectionCard>
  );
}
SourcesSection.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  locked: PropTypes.bool,
};
