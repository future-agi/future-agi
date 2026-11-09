import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import React, { useEffect } from "react";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";

import LoadingOutputSection from "../../LoadingSkeleton/LoadingOutputSection";
import useWavesurferCache from "src/hooks/use-wavesurfer-cache";

import PromptLoading from "./PromptLoading/PromptLoading";
import DefaultOutput from "./DefaultOutput";
import ResultSection from "./ResultSection";
import { usePromptWorkbenchContext } from "../../WorkbenchContext";

// IT-R4 G4 P2: a run that failed says why, inline, with Retry.
function RunError({ reason }) {
  const { saveAndRun } = usePromptWorkbenchContext();
  return (
    <Stack
      role="alert"
      data-testid="prompt-run-error"
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        p: 1.25,
        mb: 1.5,
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: "error.light",
        bgcolor: "error.lighter",
      }}
    >
      <Typography typography="s2" color="error.dark" sx={{ flex: 1 }}>
        {`Couldn't run: ${reason}`}
      </Typography>
      <Button size="small" variant="outlined" onClick={() => saveAndRun()}>
        Retry
      </Button>
    </Stack>
  );
}
RunError.propTypes = { reason: PropTypes.string.isRequired };

const OutputSection = ({
  results,
  loadingPrompt,
  promptGeneratingStatus,
  selectedVersion,
  responseFormat,
  outputFormat,
}) => {
  const theme = useTheme();
  const {
    getWaveSurferInstance,
    storeWaveSurferInstance,
    updateWaveSurferInstance,
    clearWaveSurferCache,
  } = useWavesurferCache();

  useEffect(() => {
    return () => clearWaveSurferCache();
  }, [clearWaveSurferCache]);

  if (loadingPrompt) {
    return <LoadingOutputSection />;
  }

  return (
    <Box
      sx={{
        p: 2,
        // border: "1px solid",
        // borderColor: "divider",
        borderRadius: theme.spacing(1),
        height: "100%",
        overflow: "auto",
        bgcolor: "background.neutral",
      }}
    >
      {results?.error && !promptGeneratingStatus ? (
        <RunError reason={results.error} />
      ) : null}
      <ShowComponent
        condition={Boolean(promptGeneratingStatus && !results?.output?.length)}
      >
        <PromptLoading />
      </ShowComponent>
      <ShowComponent
        condition={
          !results?.output?.length && !promptGeneratingStatus && !results?.error
        }
      >
        <DefaultOutput />
      </ShowComponent>
      <ShowComponent condition={Boolean(results?.output?.length)}>
        <ResultSection
          results={results}
          selectedVersion={selectedVersion}
          responseFormat={responseFormat}
          outputFormat={outputFormat}
          getWaveSurferInstance={getWaveSurferInstance}
          storeWaveSurferInstance={storeWaveSurferInstance}
          updateWaveSurferInstance={updateWaveSurferInstance}
        />
      </ShowComponent>
    </Box>
  );
};

OutputSection.propTypes = {
  results: PropTypes.object,
  loadingPrompt: PropTypes.bool,
  promptGeneratingStatus: PropTypes.bool,
  selectedVersion: PropTypes.object,
  responseFormat: PropTypes.object,
  outputFormat: PropTypes.string,
};

export default OutputSection;
