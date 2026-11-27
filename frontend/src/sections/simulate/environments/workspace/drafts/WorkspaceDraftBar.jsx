import PropTypes from "prop-types";
import { useCallback, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Stack, Typography } from "@mui/material";

import { isDesignEnvId } from "src/api/simulate-environments/designEnvironment";
import { draftsKey, useDrafts } from "src/api/simulate-environments/versions";
import {
  sourceGenerationPending,
  useActiveEnvironmentSourceReview,
  useEnvironmentSourceGeneration,
  useEnvironmentSourceReview,
  useEnvironmentSources,
} from "src/api/simulate-environments/sourceImports";
import { DRAFTS_COPY as COPY } from "../workspace.constants";
import { missingSnapshotMessage } from "../sources/sources.constants";
import DraftBar from "./DraftBar";
import ReviewChangesDrawer from "./ReviewChangesDrawer";

export default function WorkspaceDraftBar({
  env,
  busy,
  rebuilding,
  canEdit,
  onOpenTab,
}) {
  const [params, setParams] = useSearchParams();
  const open = params.has("reviewChanges");
  const changeOpen = useCallback(
    (value) =>
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value) next.set("reviewChanges", "1");
          else next.delete("reviewChanges");
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  const enabled = Boolean(env?.versioning?.enabled);
  const envId = enabled && !isDesignEnvId(env.id) ? env.id : null;
  const client = useQueryClient();
  const { data: drafts } = useDrafts(env?.id, { enabled });
  useEffect(() => {
    if (open && drafts && !drafts.any) changeOpen(false);
  }, [open, drafts, changeOpen]);
  const summary = useEnvironmentSources(envId);
  const { reviewId } = useActiveEnvironmentSourceReview(envId);
  const id = summary.data?.generation_review_id || reviewId;
  const { data: review } = useEnvironmentSourceReview(envId ? id : null);
  const { data: generation } = useEnvironmentSourceGeneration(
    envId ? id : null,
    summary.data?.scenario_generation || review?.scenario_generation,
  );
  const added = summary.data?.sources?.filter((source) => source.unsaved) || [];
  const affected =
    added.length > 0 ||
    (review?.receipt?.draft?.id &&
      review.receipt.draft.id === drafts?.environment?.id);
  const awaitingSuite =
    affected &&
    generation?.state === "completed" &&
    (generation.suite_draft?.id !== drafts?.suite?.id ||
      drafts?.suite?.revision < generation.suite_draft?.revision);
  useEffect(() => {
    if (awaitingSuite) client.invalidateQueries({ queryKey: draftsKey(envId) });
  }, [awaitingSuite, client, envId]);
  let sourceHold;
  if (envId && drafts?.environment && !summary.data)
    sourceHold = COPY.errors.source_generation_not_ready;
  else if (affected && summary.data?.generation_review_id && !generation)
    sourceHold = COPY.errors.source_generation_in_progress;
  else if (awaitingSuite) sourceHold = COPY.refreshingGeneratedScenarios;
  else if (affected && generation && generation.state !== "completed")
    sourceHold = sourceGenerationPending(generation)
      ? COPY.errors.source_generation_in_progress
      : missingSnapshotMessage(generation) ||
        COPY.errors.source_generation_not_ready;

  if (!drafts?.any) return null;
  const generated = affected ? generation?.generated_scenarios || [] : [];
  return (
    <>
      <DraftBar
        env={env}
        compact
        busy={busy}
        rebuilding={rebuilding}
        onReview={() => changeOpen(true)}
      />
      <ReviewChangesDrawer
        open={open}
        env={env}
        canEdit={canEdit}
        busy={busy}
        rebuilding={rebuilding}
        sourceHold={sourceHold}
        addedSources={added}
        onOpenTab={onOpenTab}
        onClose={() => changeOpen(false)}
      >
        {generated.length > 0 && (
          <Stack>
            <Typography variant="subtitle2">Generated scenarios</Typography>
            {generated.map((scenario) => (
              <Typography key={scenario.scenario_key}>
                {scenario.name}
              </Typography>
            ))}
          </Stack>
        )}
        {summary.isError && (
          <Button onClick={summary.refetch}>
            Retry loading source changes
          </Button>
        )}
      </ReviewChangesDrawer>
    </>
  );
}
WorkspaceDraftBar.propTypes = {
  env: PropTypes.object,
  busy: PropTypes.bool,
  rebuilding: PropTypes.bool,
  canEdit: PropTypes.bool,
  onOpenTab: PropTypes.func,
};
