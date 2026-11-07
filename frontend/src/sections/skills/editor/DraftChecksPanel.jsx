import PropTypes from "prop-types";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import ChecksList from "src/sections/skills/components/ChecksList";
import useDraftChecks from "./useDraftChecks";

export default function DraftChecksPanel({ files, name, onSelect }) {
  const { checks, pending, error, retry } = useDraftChecks(files, name);
  const content = files.find((file) => file.path === "SKILL.md")?.content ?? "";
  const tokens = Math.ceil(Array.from(content).length / 4);
  const overBudget = tokens > 5000;

  return (
    <Stack spacing={3}>
      <Section title="Checks" description="Errors block publishing. Warnings need your acknowledgment.">
        {pending && <ContentSkeleton variant="rows" rows={3} label="Checking files" />}
        {error && <ErrorState title="Couldn't run checks" onRetry={retry} />}
        {checks && <ChecksList checks={checks} onSelect={onSelect} dense />}
      </Section>
      <Section title="SKILL.md token budget">
        <Box sx={{ mb: 1 }}>
          <Typography variant="s1" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {tokens.toLocaleString()} ≈ tokens of 5,000
          </Typography>
          <Typography variant="s3" color="text.secondary">
            {overBudget ? "Over the recommended budget. Move supporting detail into other files." : "Estimated from character count. Supporting files load separately."}
          </Typography>
        </Box>
        <LinearProgress
          variant="determinate"
          value={Math.min(100, tokens / 50)}
          aria-label="SKILL.md token budget"
          aria-valuetext={`${tokens} estimated tokens of 5000 recommended`}
          sx={{ bgcolor: "action.hover", "& .MuiLinearProgress-bar": { bgcolor: overBudget ? "status.warning.fg" : "text.secondary" } }}
        />
      </Section>
    </Stack>
  );
}

DraftChecksPanel.propTypes = {
  files: PropTypes.arrayOf(PropTypes.shape({ path: PropTypes.string, content: PropTypes.string })).isRequired,
  name: PropTypes.string.isRequired,
  onSelect: PropTypes.func.isRequired,
};
