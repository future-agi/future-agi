import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack } from "@mui/material";

import { LoadingScreen } from "src/components/loading-screen";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
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

// A fix that never loaded. The page replaces the workspace chrome and the
// header's Back arrow comes with the fix, so each case offers its own way back;
// only a 404 means the fix is missing, and any other failure can be retried.
function FixUnavailable({ envId, error, retrying, onRetry }) {
  const status = error?.response?.status ?? error?.statusCode;
  const back = (
    <Button
      size="small"
      variant="outlined"
      component={RouterLink}
      href={paths.dashboard.simulate.environments.workspaceTab(
        envId,
        "improvements",
      )}
    >
      Back to improvements
    </Button>
  );
  if (!error || status === 404) {
    return (
      <EmptyState
        icon="solar:magic-stick-3-linear"
        title="This improvement could not be found"
        body="It may have been removed, or it belongs to another workspace."
        action={back}
      />
    );
  }
  if (status === 402) {
    return (
      <EmptyState
        icon="solar:magic-stick-3-linear"
        title="Fix my agent is not included in your plan"
        body="Upgrade your plan to open this improvement."
        action={back}
      />
    );
  }
  return (
    <EmptyState
      icon="solar:danger-triangle-linear"
      title="Could not load this improvement"
      body="Something went wrong loading this improvement. Try again."
      action={
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={retrying}
            onClick={() => onRetry?.()}
          >
            Try again
          </Button>
          {back}
        </Stack>
      }
    />
  );
}

FixUnavailable.propTypes = {
  envId: PropTypes.string.isRequired,
  error: PropTypes.object,
  retrying: PropTypes.bool,
  onRetry: PropTypes.func,
};

// One page for a fix: it follows a running one and, once it ends, becomes the
// place to decide on it. The read polls while the fix is live, so the finished
// view replaces the running one without a reload.
export default function FixRunPage({ envId, fixId, runTestId }) {
  const { fix, isLoading, isFetching, error, refetch } = useFixRun(fixId);
  const [tab, setTab] = useState("trials");
  const [reviewing, setReviewing] = useState(null);
  if (isLoading) return <LoadingScreen />;
  // A failed poll keeps the last read; only a fix never read is missing.
  if (!fix) {
    return (
      <FixUnavailable
        envId={envId}
        error={error}
        retrying={!!isFetching}
        onRetry={refetch}
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
          <FixRunningView fix={fix} runTestId={runTestId} />
        ) : (
          <FixFinishedView
            fix={fix}
            tab={tab}
            onTab={setTab}
            reviewing={reviewing}
            onPick={setReviewing}
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
  runTestId: PropTypes.string,
};
