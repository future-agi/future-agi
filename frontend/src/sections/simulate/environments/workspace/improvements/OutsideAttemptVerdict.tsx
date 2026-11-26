import { Box, Stack, Typography } from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import {
  isOutsideAttempt,
  useAttemptVerdict,
  useCodingAgentAttemptsEnabled,
} from "src/api/simulate-environments/codingAgent";
import type { AgentFixRunDetailApi } from "src/generated/api-contracts/api.schemas";

import CheckLine from "./CheckLine";
import { attemptChecks, attemptNote } from "./attemptVerdictView";

const note = { typography: "s2", color: "text.secondary" };

// On an Epoch a person's own coding agent started: who wrote the change, its own words
// for it, and Future AGI's verdict on it, check by check, as the agent reads it.
export default function OutsideAttemptVerdict({
  fix,
}: {
  fix: AgentFixRunDetailApi;
}) {
  const enabled = useCodingAgentAttemptsEnabled();
  const outside = isOutsideAttempt(fix);
  const { verdict, isLoading, error, refetch } = useAttemptVerdict(fix, {
    enabled: enabled && outside,
  });
  if (!enabled || !outside) return null;
  // An outside attempt has one: isOutsideAttempt reads its number.
  const candidate = fix.request.outside_candidate;
  if (!candidate) return null;
  const { number, summary } = candidate;
  const checks = attemptChecks(verdict, fix.request);
  const said = attemptNote(verdict);
  return (
    <Box sx={{ px: 3, pt: 2.5 }}>
      <Section
        variant="panel"
        data-testid="outside-attempt"
        title={`Attempt ${number} from your coding agent`}
        description={
          // A run with too few failing cases holds none back.
          fix.request.held_back_scenario_keys?.length
            ? "Your coding agent wrote this change. Future AGI checks every fix on cases your agent wasn't given."
            : "Your coding agent wrote this change. Future AGI checks it on the run's failing cases and on the cases that pass today."
        }
        sx={{ p: 2 }}
      >
        {summary && (
          <Box sx={{ mb: 1.5 }}>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Your agent&apos;s note
            </Typography>
            <Typography sx={{ typography: "s2", whiteSpace: "pre-wrap" }}>
              {summary}
            </Typography>
          </Box>
        )}
        {isLoading && <ContentSkeleton variant="panel" />}
        {error && (
          <ErrorState
            title="Couldn't load this attempt's verdict"
            detail="The attempt itself is unaffected."
            onRetry={() => refetch()}
            sx={{ py: 2 }}
          />
        )}
        {checks.length > 0 && (
          <Stack spacing={0.75} data-testid="outside-attempt-checks">
            {checks.map((check) => (
              <CheckLine
                key={check.name}
                check={check}
                variant="s2"
                nameWidth={180}
              />
            ))}
          </Stack>
        )}
        {said && <Typography sx={{ ...note, mt: 1 }}>{said}</Typography>}
      </Section>
    </Box>
  );
}
