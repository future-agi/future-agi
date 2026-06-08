import { Box, Button, Chip, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { METRIC_TAB_IDS } from "./constants";

const PromptMetricsOnboardingFocusPanel = ({
  activeTab,
  isCompletingLoop,
  isOnboarding,
  onCompleteLoop,
  onOpenFilters,
  onOpenLinkedTraces,
}) => {
  if (!isOnboarding) {
    return null;
  }

  return (
    <Box
      data-testid="prompt-metrics-onboarding-focus"
      sx={{
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        bgcolor: "background.paper",
        mt: 1.5,
        p: 1.5,
      }}
    >
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={1.5}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", md: "center" }}
      >
        <Stack spacing={0.75} sx={{ minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            <Chip size="small" label="Prompt setup" />
            <Chip size="small" variant="outlined" label="Metrics" />
            <Chip size="small" variant="outlined" label="Step 6 of 6" />
          </Stack>
          <Box>
            <Typography variant="subtitle2">
              Review the prompt quality signal
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Check evaluation averages, filter weak versions, and inspect
              linked traces before choosing the next prompt change.
            </Typography>
          </Box>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            <Chip size="small" variant="outlined" label="Averages" />
            <Chip size="small" variant="outlined" label="Filters" />
            <Chip size="small" label="Daily signal" />
          </Stack>
        </Stack>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button variant="outlined" onClick={onOpenFilters}>
            Filter weak versions
          </Button>
          <Button
            variant="outlined"
            disabled={activeTab === METRIC_TAB_IDS.LINKED_TRACES}
            onClick={onOpenLinkedTraces}
          >
            Linked Traces
          </Button>
          <Button
            variant="contained"
            disabled={isCompletingLoop}
            onClick={onCompleteLoop}
          >
            {isCompletingLoop ? "Finishing..." : "Finish setup"}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
};

PromptMetricsOnboardingFocusPanel.propTypes = {
  activeTab: PropTypes.string,
  isCompletingLoop: PropTypes.bool,
  isOnboarding: PropTypes.bool,
  onCompleteLoop: PropTypes.func,
  onOpenFilters: PropTypes.func,
  onOpenLinkedTraces: PropTypes.func,
};

export default PromptMetricsOnboardingFocusPanel;
