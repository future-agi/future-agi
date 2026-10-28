import { Box, Stack, Typography } from "@mui/material";

import { SAMPLE_CALL_GRAPH } from "src/api/simulate-environments/_fixtures/detail/callGraph";

import SoonChip from "../../../../components/SoonChip";
import UiOnlyFlag from "../../../../components/UiOnlyFlag";
import useUiSample from "../../../../hooks/useUiSample";
import CallGraph, { GraphLegend } from "./CallGraph";

// The Graph tab. The API has no call graph, so a real call gets the soon
// note; `?uiSample=graph` shows Vel's sample drawing, flagged.
export default function CallGraphPane() {
  const sample = useUiSample("graph");

  if (!sample) {
    return (
      <Stack alignItems="center" spacing={1} sx={{ p: 4 }}>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          The call graph isn&apos;t in the API yet.
        </Typography>
        <SoonChip label="Call graph" what="The call graph" />
      </Stack>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Call graph
        </Typography>
        <UiOnlyFlag />
      </Stack>
      <GraphLegend />
      <CallGraph
        spine={SAMPLE_CALL_GRAPH.spine}
        branches={SAMPLE_CALL_GRAPH.branches}
      />
      <Box
        sx={{
          mt: 1.5,
          p: 1.5,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.subtitle",
            mb: 0.5,
          }}
        >
          Falcon analysis
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {SAMPLE_CALL_GRAPH.analysis}
        </Typography>
      </Box>
    </Box>
  );
}
