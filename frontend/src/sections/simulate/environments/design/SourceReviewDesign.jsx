import PropTypes from "prop-types";
import { cloneElement, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import PageHeader from "src/components/page-header";
import SectionCard from "../components/SectionCard";
import { PlanLine, PlanRow } from "./planParts";

const CHANNELS = { chat: "Chat", voice: "Voice" };

/* The design page before the builder runs: the brief and the files to approve.
   The parts the builder proposes come after the sources are approved. */
export default function SourceReviewDesign({ requirements, modality, review }) {
  const [actionsEl, setActionsEl] = useState(null);
  const rows = [
    ["Description", requirements || "None"],
    ["Channel", CHANNELS[modality] || CHANNELS.chat],
    ["Agent", "None yet. Connect one after you save."],
  ];
  return (
    <Box>
      <PageHeader
        title="Review the sources"
        description="Nothing is designed until you approve the sources. Then the builder drafts the design for you to review."
        actions={<Box ref={setActionsEl} sx={{ display: "flex" }} />}
        sx={{ mb: 0 }}
      />

      <SectionCard title="Brief" sx={{ mt: 2 }}>
        <Box sx={{ py: 0.5 }}>
          {rows.map(([label, value]) => (
            <Stack key={label} direction="row" sx={{ px: 1.5, py: 0.75 }}>
              <Typography
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 120,
                  flexShrink: 0,
                }}
              >
                {label}
              </Typography>
              <Typography
                sx={{
                  typography: "s1",
                  flex: 1,
                  minWidth: 0,
                  whiteSpace: "pre-wrap",
                }}
              >
                {value}
              </Typography>
            </Stack>
          ))}
        </Box>
      </SectionCard>

      <Stack spacing={3} sx={{ mt: 3 }}>
        <PlanRow label="Sources" count="">
          <Box sx={{ px: 1.5, py: 1.5 }}>
            {actionsEl && cloneElement(review, { actionsEl })}
          </Box>
        </PlanRow>
        <PlanRow label="Design" count="">
          <PlanLine empty>
            Records, tools, rules, people and scenarios are proposed here after
            you approve the sources.
          </PlanLine>
        </PlanRow>
      </Stack>
    </Box>
  );
}

SourceReviewDesign.propTypes = {
  requirements: PropTypes.string,
  modality: PropTypes.string,
  review: PropTypes.element.isRequired,
};
