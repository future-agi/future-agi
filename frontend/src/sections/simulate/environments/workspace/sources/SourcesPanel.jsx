import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import { Alert, Button, Chip, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  sourceSummaryKey,
  useCreateSourceReview,
  useEnvironmentSources,
  useSourceCapabilities,
} from "src/api/simulate-environments/sourceImports";
import { draftsKey } from "src/api/simulate-environments/versions";
import BuildSourceFields from "../../buildDrawer/forms/shared/BuildSourceFields";
import useBuildSources from "../../buildDrawer/forms/shared/useBuildSources";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import SourceReviewPanel from "./SourceReviewPanel";

const purposes = {
  reference: "Reference",
  policy: "Policy",
  seed_records: "Structured scenario data",
};

export default function SourcesPanel({ env, locked }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { canEdit } = useSimulationAccess();
  const writable = canEdit && !locked;
  const sources = useBuildSources();
  const { addKnowledgeSelection } = sources;
  const storageKey = `harness-environment-source-review:${env.id}`;
  const [reviewId, setReviewId] = useState(() =>
    sessionStorage.getItem(storageKey),
  );
  const request = useRef(null);
  const consumedSelection = useRef(null);
  const create = useCreateSourceReview();
  const summary = useEnvironmentSources(env.id);
  const capabilities = useSourceCapabilities(env.id);

  useEffect(() => {
    const selection = location.state?.environmentSourceSelection;
    if (!writable || !selection || consumedSelection.current === location.key)
      return;
    consumedSelection.current = location.key;
    addKnowledgeSelection(
      { kind: "knowledge_base", ...selection },
      "Selected Knowledge Base",
    );
    navigate(`${location.pathname}${location.search}`, {
      replace: true,
      state: {},
    });
  }, [
    addKnowledgeSelection,
    location.key,
    location.pathname,
    location.search,
    location.state,
    navigate,
    writable,
  ]);

  const prepare = () => {
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
    if (request.current?.signature !== signature) {
      request.current = { signature, key: harnessIdempotencyKey() };
    }
    create.mutate(
      { ...body, idempotency_key: request.current.key },
      {
        onSuccess: (review) => {
          sessionStorage.setItem(storageKey, review.id);
          setReviewId(review.id);
        },
      },
    );
  };
  const close = () => {
    request.current = null;
    sessionStorage.removeItem(storageKey);
    setReviewId(null);
  };
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: sourceSummaryKey(env.id) });
    queryClient.invalidateQueries({ queryKey: draftsKey(env.id) });
  };
  const error = create.error || summary.error || capabilities.error;

  return (
    <Stack spacing={2} sx={{ p: 3, maxWidth: 960 }}>
      <Typography variant="h5">Sources</Typography>
      <Typography color="text.secondary">
        Use files and Knowledge Base content as input for scenarios. Adding a
        source updates the draft. Save creates a new Environment version and
        preserves the original version and source files.
      </Typography>
      {error && (
        <Alert severity="error">
          {error.message || error.detail || "Could not prepare sources."}
        </Alert>
      )}
      {reviewId ? (
        <SourceReviewPanel
          reviewId={reviewId}
          ownerId={env.id}
          canEdit={writable && capabilities.isSuccess}
          environmentBasis={capabilities.data}
          acceptLabel="Add to draft"
          onAccepted={refresh}
          onCancelled={close}
          onDismiss={close}
        />
      ) : (
        writable && (
          <>
            <BuildSourceFields sources={sources} disabled={create.isPending} />
            <Button
              variant="contained"
              sx={{ alignSelf: "start" }}
              disabled={
                create.isPending ||
                sources.blocked ||
                !sources.selection ||
                !capabilities.data
              }
              onClick={prepare}
            >
              {create.isPending ? "Preparing sources…" : "Review sources"}
            </Button>
          </>
        )
      )}
      <Typography variant="h6">Current sources</Typography>
      {summary.isLoading ? (
        <Typography color="text.secondary">Loading sources…</Typography>
      ) : summary.data?.sources?.length ? (
        summary.data.sources.map((source) => (
          <Stack
            key={source.source_id}
            direction="row"
            spacing={1}
            alignItems="center"
          >
            <Typography>{source.source_id}</Typography>
            <Chip
              size="small"
              label={purposes[source.purpose] || source.purpose}
            />
            {summary.data.draft && (
              <Chip size="small" color="warning" label="Draft" />
            )}
          </Stack>
        ))
      ) : (
        <Typography color="text.secondary">No sources added yet.</Typography>
      )}
    </Stack>
  );
}

SourcesPanel.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  locked: PropTypes.bool,
};
