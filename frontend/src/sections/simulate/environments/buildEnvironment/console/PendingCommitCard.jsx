import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";

import { useAnswerPendingCommit } from "src/api/simulate-environments/versions";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { PENDING_COMMIT_COPY as COPY } from "../build.constants";

const SHORT_SHA = 7;

/**
 * A new commit on the agent's branch, offered to the environment: Sync makes it
 * the next agent version, Skip sets it aside. Shown in the chat and the agent
 * versions panel; answering in one clears both.
 */
export default function PendingCommitCard({ envId, commit }) {
  const answer = useAnswerPendingCommit(envId);
  const title = COPY.title(
    String(commit.commitSha).slice(0, SHORT_SHA),
    commit.ref || COPY.defaultBranch,
  );
  return (
    <Box
      data-testid="pending-commit-card"
      component="section"
      aria-label={title}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        p: 1.5,
      }}
    >
      <Stack spacing={1}>
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s2" }}>{COPY.question}</Typography>
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            variant="contained"
            disabled={answer.isPending}
            onClick={() => answer.mutate("sync")}
          >
            {COPY.sync}
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={answer.isPending}
            onClick={() => answer.mutate("skip")}
          >
            {COPY.skip}
          </Button>
        </Stack>
        {answer.isError && (
          <Typography
            role="alert"
            sx={{ typography: "s2", color: "error.main" }}
          >
            {errorMessage(answer.error)}
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

PendingCommitCard.propTypes = {
  envId: PropTypes.string.isRequired,
  commit: PropTypes.shape({
    commitSha: PropTypes.string.isRequired,
    ref: PropTypes.string,
  }).isRequired,
};
