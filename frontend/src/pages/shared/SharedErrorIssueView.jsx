import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import ErrorFeedDetailView from "src/pages/dashboard/error-feed/ErrorFeedDetailView";
import { SharedIssueContext } from "src/pages/dashboard/error-feed/sharedIssueContext";

// A shared Error Feed issue: the issue page itself, read-only, fed by the
// resolved share payload instead of signed-in reads.
export default function SharedErrorIssueView({ token, data }) {
  const shared = useMemo(() => ({ token, ...data }), [token, data]);
  return (
    <SharedIssueContext.Provider value={shared}>
      <Box sx={{ flex: 1, display: "flex", minHeight: 0, overflow: "hidden" }}>
        <ErrorFeedDetailView />
      </Box>
    </SharedIssueContext.Provider>
  );
}

SharedErrorIssueView.propTypes = {
  token: PropTypes.string.isRequired,
  data: PropTypes.shape({
    issue: PropTypes.object,
    overview: PropTypes.object,
    traces: PropTypes.object,
    trends: PropTypes.object,
    sidebar: PropTypes.object,
  }).isRequired,
};
