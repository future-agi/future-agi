import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  sourceSummaryKey,
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
  const [, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { canEdit } = useSimulationAccess();
  const writable = canEdit && !locked;
  const sources = useBuildSources();
  const { reviewId, setReviewId } = useActiveEnvironmentSourceReview(env.id);
  const [reviewOpen, setReviewOpen] = useState(Boolean(reviewId));
  const [selecting, setSelecting] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const [kbOpen, setKbOpen] = useState(false);
  const [pdfPolicy, setPdfPolicy] = useState(false);
  const [limitError, setLimitError] = useState("");
  const request = useRef(null);
  const consumedSelection = useRef(null);
  const create = useCreateSourceReview();
  const summary = useEnvironmentSources(env.id);
  const capabilities = useSourceCapabilities(env.id);
  const rows = summary.data?.sources || [];

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
      document_purposes: Object.fromEntries(
        sources.uploads.done
          .filter((file) => pdfPolicy && /\.pdf$/i.test(file.name))
          .map((file) => [file.documentId, "policy"]),
      ),
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

  function reviewChanges() {
    setReviewOpen(false);
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("reviewChanges", "1");
        return next;
      },
      { replace: true },
    );
  }

  function accepted() {
    queryClient.invalidateQueries({ queryKey: sourceSummaryKey(env.id) });
    queryClient.invalidateQueries({ queryKey: draftsKey(env.id) });
    enqueueSnackbar("Sources added to the draft.", {
      variant: "success",
      action: (
        <Button color="inherit" size="small" onClick={reviewChanges}>
          Review changes
        </Button>
      ),
    });
  }

  const error =
    limitError || summary.error?.message || capabilities.error?.message;
  const draftGeneration =
    rows.some((source) => source.unsaved) && summary.data?.scenario_generation;
  const resumable = draftGeneration && summary.data?.generation_review_id;
  const pendingReview = reviewId || selecting;
  const generationHeld =
    draftGeneration &&
    ["failed", "queued", "running"].includes(draftGeneration.state);
  return (
    <SectionCard
      id="environment-sources"
      sx={{ mb: 2 }}
      title={SOURCES_COPY.title(rows.length)}
      subtitle={
        !rows.length
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
              disabled={
                Boolean(pendingReview || generationHeld) || !capabilities.data
              }
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              onClick={(event) => setAnchor(event.currentTarget)}
            >
              Add source
            </Button>
          )}
        </Stack>
      }
    >
      {error && <Alert severity="error">{error}</Alert>}
      {generationHeld && !pendingReview && (
        <Alert severity={draftGeneration.state === "failed" ? "error" : "info"}>
          {draftGeneration.state === "failed"
            ? "Scenario generation failed. Open the source review to retry."
            : draftGeneration.state === "stale"
              ? "This source generation is out of date. Prepare the sources again."
              : "Scenario generation is in progress."}
        </Alert>
      )}
      {summary.isLoading && (
        <Typography sx={{ p: 1.5 }}>Loading sources…</Typography>
      )}
      <SourcesTable
        envId={env.id}
        sources={rows}
        generation={summary.data?.scenario_generation}
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
          pdfPolicy={pdfPolicy}
          onPdfPolicyChange={setPdfPolicy}
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
          <Box sx={{ px: 2.5, py: 1.5 }}>
            <SourceReviewPanel
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
                <Typography>{file.name}</Typography>
                <Typography color="text.secondary">
                  {
                    {
                      failed: "Upload failed",
                      uploading: "Uploading…",
                      done: "Uploaded",
                    }[file.status]
                  }
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
              <Typography key={item.selection.kb_id}>{item.label}</Typography>
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
              !sources.blocked && (
                <Typography role="status">
                  {sources.selection
                    ? "Preparing sources…"
                    : "Select files to prepare a review."}
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
