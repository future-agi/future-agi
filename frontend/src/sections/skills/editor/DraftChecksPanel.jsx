import PropTypes from "prop-types";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import ChecksList from "src/sections/skills/components/ChecksList";


export default function DraftChecksPanel({ files, onSelect, validation }) {
  const { checks, previous, pending, error, retry } = validation;
  const findings = checks ?? previous;
  const warnings = findings?.summary?.warnings || 0;
  const content = files.find((file) => file.path === "SKILL.md")?.content ?? "";
  const tokens = Math.ceil(Array.from(content).length / 4);
  const overBudget = tokens > 5000;

  return (
    <Stack spacing={3}>
      <Section title={warnings ? `Checks · ${warnings} ${warnings === 1 ? 'warning' : 'warnings'}` : 'Checks'} description="Errors block publishing. Warnings need your acknowledgment.">
        {pending && <StatusBadge status="checking" label="Checking your latest changes…" />}
        {pending && !previous && <ContentSkeleton variant="rows" rows={3} label="Checking files" />}
        {error && <ErrorState title="Couldn't run checks" onRetry={retry} />}
        {findings && <Box sx={{ color: checks ? 'text.primary' : 'text.secondary', '& section[aria-label="Warnings"] li .MuiStack-root > span:first-of-type': { display: 'none' } }}>
          {!checks && <Typography variant="s3" component="p" color="text.secondary" sx={{ mb: 0.5 }}>Previous result</Typography>}
          <ChecksList checks={findings} onSelect={onSelect} dense />
        </Box>}
      </Section>
      <Section title="SKILL.md token budget">
        <Stack spacing={0.5} sx={{ mb: 1 }}>
          <Typography variant="s1" sx={{ fontVariantNumeric: "tabular-nums" }}>
            ≈{tokens.toLocaleString()} of 5,000 tokens
          </Typography>
          <Typography variant="s3" color="text.secondary">
            {overBudget ? "Over the recommended budget. Move supporting detail into other files." : "Estimated from character count. Supporting files load separately."}
          </Typography>
        </Stack>
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
  validation: PropTypes.object.isRequired,
  onSelect: PropTypes.func.isRequired,
};
