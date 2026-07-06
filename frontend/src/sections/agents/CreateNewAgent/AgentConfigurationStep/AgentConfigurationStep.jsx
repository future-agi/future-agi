import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { Suspense } from "react";
import { useWatch } from "react-hook-form";
import { AGENT_TYPES } from "../../constants";
import FormSkeleton from "./FormSkeleton";
const AgentChatForm = React.lazy(() => import("./AgentChatForm"));
const AgentVoiceForm = React.lazy(() => import("./AgentVoiceForm"));

const AgentConfigurationStep = ({ control, getValues }) => {
  const agentType = useWatch({
    control,
    name: "agentType",
    defaultValue: getValues("agentType"),
  });
  const renderForm = () => {
    if (agentType === AGENT_TYPES.CHAT) {
      return (
        <Suspense fallback={<FormSkeleton />}>
          <AgentChatForm control={control} />
        </Suspense>
      );
    } else {
      return (
        <Suspense fallback={<FormSkeleton />}>
          <AgentVoiceForm />
        </Suspense>
      );
    }
  };
  return (
    <Box display={"flex"} flexDirection={"column"} gap={3}>
      <Box display={"flex"} flexDirection={"column"}>
        <Typography
          typography="m2"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Agent Configuration
        </Typography>
        <Typography
          typography="s1"
          fontWeight="fontWeightRegular"
          color="text.secondary"
        >
          Configure specific settings and provider details
        </Typography>
      </Box>
      {renderForm()}
    </Box>
  );
};

AgentConfigurationStep.propTypes = {
  control: PropTypes.object,
  getValues: PropTypes.func,
};

export default AgentConfigurationStep;
