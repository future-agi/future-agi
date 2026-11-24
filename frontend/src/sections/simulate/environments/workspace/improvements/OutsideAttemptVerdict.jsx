import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import Section from "src/components/section";
import {
  isOutsideAttempt,
  useAttemptVerdict,
  useCodingAgentAttemptsEnabled,
} from "src/api/simulate-environments/codingAgent";

import CheckLine from "./CheckLine";
import {
  attemptChecks,
  attemptNote,
  refusedBeforeChecks,
  refusedCheck,
} from "./attemptVerdictView";

const note = { typography: "s2", color: "text.secondary" };

// On an Epoch a person's own coding agent started: who wrote the change, its own words
// for it, and Future AGI's verdict on it, check by check, as the agent reads it.
export default function OutsideAttemptVerdict({ fix }) {
  const enabled = useCodingAgentAttemptsEnabled();
  const outside = isOutsideAttempt(fix);
  const { verdict, isLoading, error } = useAttemptVerdict(fix, {
    enabled: enabled && outside,
  });
  if (!enabled || !outside) return null;
  const { number, summary } = fix.request.outside_candidate;
  const checks = refusedBeforeChecks(verdict)
    ? [refusedCheck(fix.request)]
    : attemptChecks(verdict, fix.request);
  const said = attemptNote(verdict);
  return (
    <Box sx={{ px: 3, pt: 2.5 }}>
      <Section
        variant="panel"
        data-testid="outside-attempt"
        title={`Attempt ${number} from your coding agent`}
        description="Your coding agent wrote this change. Future AGI checks every fix on cases your agent wasn't given."
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
          <Typography sx={note}>
            Could not read this attempt&apos;s verdict. Reload the page to try
            again.
          </Typography>
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

OutsideAttemptVerdict.propTypes = { fix: PropTypes.object.isRequired };
