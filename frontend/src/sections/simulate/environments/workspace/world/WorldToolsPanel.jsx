import PropTypes from "prop-types";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Box } from "@mui/material";
import CapabilityGraph from "../overview/CapabilityGraph";
import WorldInternalsSection from "../contract/WorldInternalsSection";
import SourcesSection from "../sources/SourcesSection";
import SourceDrawer from "../sources/SourceDrawer";
import ReviewRefreshDrawer from "../sources/ReviewRefreshDrawer";
import useKnowledgeBaseChanges from "../sources/useKnowledgeBaseChanges";

const SOURCE_PARAMS = ["source", "sourceTab", "at"];

/**
 * World & tools: what the world is (the capability graph), the files it is
 * built from, and the tables and tools a run reads from.
 */
export default function WorldToolsPanel({
  env,
  envState,
  patch,
  locked = false,
  graphData,
  onReviewChanges,
}) {
  const [params, setParams] = useSearchParams();
  const [refreshing, setRefreshing] = useState(null);
  useKnowledgeBaseChanges(env.id);
  const reviewRefresh = locked
    ? undefined
    : (source) => setRefreshing(source.source_id);
  const hasWorld = !!envState?.agent || (env.tools?.length || 0) > 0;
  const sourceId = params.get("source");
  const sourceTab = params.get("sourceTab");
  const at = params.get("at");
  const closeSource = () =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        SOURCE_PARAMS.forEach((key) => next.delete(key));
        return next;
      },
      { replace: true },
    );
  return (
    <Box sx={{ p: 2 }}>
      {hasWorld && (
        <Box sx={{ mb: 2 }}>
          <CapabilityGraph
            env={env}
            envState={envState}
            data={graphData}
            defaultOpen
          />
        </Box>
      )}
      <SourcesSection
        envId={env.id}
        readOnly={locked}
        onReviewChanges={onReviewChanges}
        onReviewRefresh={reviewRefresh}
      />
      <WorldInternalsSection
        env={env}
        envState={envState}
        patch={patch}
        locked={locked}
      />
      {/* Keyed so a new file, tab or citation opens fresh. */}
      {sourceId && (
        <SourceDrawer
          key={`${sourceId}:${sourceTab}:${at}`}
          envId={env.id}
          sourceId={sourceId}
          tab={sourceTab}
          at={at}
          readOnly={locked}
          onClose={closeSource}
          onReviewRefresh={reviewRefresh}
        />
      )}
      {refreshing && (
        <ReviewRefreshDrawer
          envId={env.id}
          sourceId={refreshing}
          onClose={() => setRefreshing(null)}
        />
      )}
    </Box>
  );
}

WorldToolsPanel.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    tools: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string })),
  }).isRequired,
  envState: PropTypes.shape({
    agent: PropTypes.shape({ name: PropTypes.string }),
  }).isRequired,
  patch: PropTypes.func,
  locked: PropTypes.bool,
  // Real capability-graph data for a backed env; undefined otherwise.
  graphData: PropTypes.object,
  // Opens Review changes, from the snackbar after proposals are added.
  onReviewChanges: PropTypes.func,
};
