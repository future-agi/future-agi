import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import SectionCard from "../components/SectionCard";
import { DRAFT_PREVIEW_COPY as COPY } from "../useTemplate.constants";

// A deliberately local reader: never mount server-backed workspace controls
// with a library template id, and never label generated sample data as a run.
export default function TemplateDraftPreview({ draft, onClose }) {
  return (
    <Box role="region" aria-label={COPY.title}>
      <SectionCard
        title={COPY.title}
        subtitle={COPY.subtitle}
        wrap
        action={<Button onClick={onClose}>{COPY.close}</Button>}
      >
        <Stack spacing={1.5} sx={{ p: 2.5 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {COPY.agent}
          </Typography>
          <Typography sx={{ typography: "s2" }}>
            {draft.state.agent.name}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {COPY.scenarios}
          </Typography>
          <Box
            component="ul"
            sx={{ m: 0, pl: 2.5, maxHeight: 320, overflowY: "auto" }}
          >
            {draft.state.scenarios.map((scenario) => (
              <Box
                component="li"
                key={scenario.id}
                sx={{ mb: 1.5, overflowWrap: "anywhere" }}
              >
                <Typography
                  sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
                >
                  {scenario.name}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {scenario.task}
                </Typography>
              </Box>
            ))}
          </Box>
        </Stack>
      </SectionCard>
    </Box>
  );
}

TemplateDraftPreview.propTypes = {
  draft: PropTypes.shape({
    state: PropTypes.shape({
      agent: PropTypes.shape({ name: PropTypes.string.isRequired }).isRequired,
      scenarios: PropTypes.arrayOf(
        PropTypes.shape({
          id: PropTypes.string.isRequired,
          name: PropTypes.string.isRequired,
          task: PropTypes.string.isRequired,
        }),
      ).isRequired,
    }).isRequired,
  }).isRequired,
  onClose: PropTypes.func.isRequired,
};
