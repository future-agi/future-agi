import { Box, Button, Link, Stack, Typography, useTheme } from "@mui/material";
import React, { useEffect } from "react";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useManagedModelsStatus } from "src/sections/gateway/models/hooks/useManagedModels";
import {
  CREDITS_ROUTE,
  creditsRouteFrom,
  useGatewayList,
} from "src/components/custom-model-dropdown/noUsableModel";

import LoadingOutputSection from "../../LoadingSkeleton/LoadingOutputSection";
import useWavesurferCache from "src/hooks/use-wavesurfer-cache";

import PromptLoading from "./PromptLoading/PromptLoading";
import DefaultOutput from "./DefaultOutput";
import ResultSection from "./ResultSection";
import { usePromptWorkbenchContext } from "../../WorkbenchContext";
import {
  API_KEY_SENTENCE,
  CREDITS_SENTENCE,
  GATEWAY_KEYS_LINK_LABEL,
  GATEWAY_MODELS_LINK_LABEL,
  PROVIDER_KEY_LINK_LABEL,
  PROVIDER_KEY_SENTENCE,
  creditsOnFromStatus,
  isProviderKeyRefusal,
  refusalLead,
} from "./runRefusal";

const LINKS = {
  [PROVIDER_KEY_LINK_LABEL]: paths.dashboard.settings.aiProviders,
  [GATEWAY_MODELS_LINK_LABEL]: paths.dashboard.gateway.models,
  [GATEWAY_KEYS_LINK_LABEL]: paths.dashboard.gateway.keys,
};

// What works, for each credits path the model menus name.
const SENTENCES = {
  [CREDITS_ROUTE.NONE]: PROVIDER_KEY_SENTENCE,
  [CREDITS_ROUTE.TRY_IT]: CREDITS_SENTENCE,
  [CREDITS_ROUTE.API_KEY]: API_KEY_SENTENCE,
};

// The copy sentence with its route names as links; the text stays word for
// word. With credits on, the gateway list says which path works: Try it,
// unless delegated_playground_available is false (then an API key), by the
// model menus' rule.
function WhatWorks() {
  const status = useManagedModelsStatus();
  const creditsOn = creditsOnFromStatus(status);
  const { data: gateways } = useGatewayList({ enabled: creditsOn });
  const route = creditsOn
    ? creditsRouteFrom(status?.data, gateways)
    : CREDITS_ROUTE.NONE;
  const parts = (SENTENCES[route] ?? PROVIDER_KEY_SENTENCE).split(
    new RegExp(`(${Object.keys(LINKS).join("|")})`),
  );
  return (
    <Typography
      typography="s2"
      color="text.primary"
      data-testid="prompt-run-what-works"
    >
      {parts.map((part, i) =>
        LINKS[part] ? (
          <Link
            key={i}
            component={RouterLink}
            href={LINKS[part]}
            underline="always"
            color="primary.dark"
            fontWeight={"fontWeightMedium"}
          >
            {part}
          </Link>
        ) : (
          <React.Fragment key={i}>{part}</React.Fragment>
        ),
      )}
    </Typography>
  );
}

// IT-R4 G4 P2: a run that failed says why, inline, with Retry.
function RunError({ reason }) {
  const { saveAndRun } = usePromptWorkbenchContext();
  if (isProviderKeyRefusal(reason)) {
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
        <Stack sx={{ flex: 1 }} gap={0.5}>
          <Typography typography="s2" color="error.dark">
            {`Couldn't run: ${refusalLead(reason)}`}
          </Typography>
          <WhatWorks />
        </Stack>
        <Button size="small" variant="outlined" onClick={() => saveAndRun()}>
          Retry
        </Button>
      </Stack>
    );
  }
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

  // A streamed refusal lands in the output cell; it also gets the inline
  // explanation (the cell stays as it is).
  const runError =
    results?.error ??
    results?.output?.find(
      (o) => o?.status === "error" && isProviderKeyRefusal(o?.text),
    )?.text;

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
      {runError && !promptGeneratingStatus ? (
        <RunError reason={runError} />
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
