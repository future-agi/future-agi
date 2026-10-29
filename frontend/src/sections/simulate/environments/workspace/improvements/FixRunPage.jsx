import PropTypes from "prop-types";
import { useState } from "react";
import { Box } from "@mui/material";

import { LoadingScreen } from "src/components/loading-screen";
import {
  FIX_LIVE_STATES,
  useFixRun,
} from "src/api/simulate-environments/fixRuns";

import EmptyState from "../../components/EmptyState";

import FixHeader from "./FixHeader";
import FixRunningView from "./FixRunningView";
import FixFinishedView, { FinishedActions } from "./FixFinishedView";
import FixChangesTab from "./FixChangesTab";
import FixCostTab from "./FixCostTab";
import FixScenariosTab from "./FixScenariosTab";

// One page for a fix: it follows a running one and, once it ends, becomes the
// place to decide on it. The read polls while the fix is live, so the finished
// view replaces the running one without a reload.
export default function FixRunPage({ envId, fixId }) {
  const { fix, isLoading, error } = useFixRun(fixId);
  const [tab, setTab] = useState("trials");
  const [reviewing, setReviewing] = useState(null);
  if (isLoading) return <LoadingScreen />;
  if (error || !fix) {
    return (
      <EmptyState
        icon="solar:magic-stick-3-linear"
        title="This improvement could not be found"
        body="It may have been removed, or it belongs to another workspace."
      />
    );
  }
  const live = FIX_LIVE_STATES.has(fix.state);
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <FixHeader
        fix={fix}
        envId={envId}
        actions={
          live ? null : (
            <FinishedActions
              fix={fix}
              onReview={(candidateId) => {
                setReviewing(candidateId);
                setTab("changes");
              }}
            />
          )
        }
      />
      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {live ? (
          <FixRunningView fix={fix} />
        ) : (
          <FixFinishedView
            fix={fix}
            tab={tab}
            onTab={setTab}
            renderTab={(id) => {
              if (id === "changes") {
                return (
                  <FixChangesTab
                    key={reviewing}
                    fix={fix}
                    candidateId={reviewing}
                  />
                );
              }
              if (id === "scenarios") {
                return <FixScenariosTab fix={fix} candidateId={reviewing} />;
              }
              if (id === "cost") return <FixCostTab fix={fix} />;
              return null;
            }}
          />
        )}
      </Box>
    </Box>
  );
}

FixRunPage.propTypes = {
  envId: PropTypes.string.isRequired,
  fixId: PropTypes.string.isRequired,
};
