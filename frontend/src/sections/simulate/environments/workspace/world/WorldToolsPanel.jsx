import PropTypes from "prop-types";
import { useState } from "react";
import { Box } from "@mui/material";
import CapabilityGraph from "../overview/CapabilityGraph";
import WorldInternalsSection from "../contract/WorldInternalsSection";
import SourcesSection from "../sources/SourcesSection";
import SourceDrawer from "../sources/SourceDrawer";
import ReviewRefreshDrawer from "../sources/ReviewRefreshDrawer";
import useKnowledgeBaseChanges from "../sources/useKnowledgeBaseChanges";
import useSourceParams from "../sources/useSourceParams";

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
  const { sourceId, sourceTab, at, closeSource } = useSourceParams();
  // The last file opened stays after the URL drops it, so its drawer slides shut.
  const [file, setFile] = useState(null);
  if (
    sourceId &&
    (file?.sourceId !== sourceId || file.tab !== sourceTab || file.at !== at)
  )
    setFile({ sourceId, tab: sourceTab, at });
  const [refreshing, setRefreshing] = useState({ id: null, open: false });
  // Supply the records closes the file and opens Sources' Add source menu.
  const [supplying, setSupplying] = useState(false);
  useKnowledgeBaseChanges(env.id);
  const reviewRefresh = locked
    ? undefined
    : (source) => setRefreshing({ id: source.source_id, open: true });
  const hasWorld = !!envState?.agent || (env.tools?.length || 0) > 0;
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
        supplying={supplying && !sourceId}
        onSupplied={() => setSupplying(false)}
      />
      <WorldInternalsSection
        env={env}
        envState={envState}
        patch={patch}
        locked={locked}
      />
      {/* Always mounted so opening and closing both slide. */}
      <SourceDrawer
        envId={env.id}
        open={Boolean(sourceId)}
        sourceId={file?.sourceId}
        tab={file?.tab}
        at={file?.at}
        readOnly={locked}
        onClose={closeSource}
        onReviewRefresh={reviewRefresh}
        onSupply={() => {
          closeSource();
          setSupplying(true);
        }}
      />
      <ReviewRefreshDrawer
        envId={env.id}
        open={refreshing.open}
        sourceId={refreshing.id}
        onClose={() => setRefreshing((one) => ({ ...one, open: false }))}
      />
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
