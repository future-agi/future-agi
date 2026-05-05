import { Box, Chip, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { languageOptions } from "src/components/agent-definitions/helper";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import LanguageMultiSelect from "./LanguageMultiSelect";
import HeadingAndSubHeading from "src/components/HeadingAndSubheading/HeadingAndSubheading";

const AgentBasicInfo = ({ control }) => {
  const { isOSS } = useDeploymentMode();
  const agentTypeOptions = [
    ...(!isOSS
      ? [
          {
            label: "Voice",
            value: "voice",
          },
        ]
      : []),
    {
      label: "Chat",
      value: "text",
    },
  ];
  return (
    <Box display={"flex"} flexDirection={"column"} gap={3}>
      <Box display={"flex"} flexDirection={"column"}>
        <Typography
          typography="m2"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Basic Information
        </Typography>
        <Typography
          typography="s1"
          fontWeight="fontWeightRegular"
          color="text.disabled"
        >
          Set up the basic details for your agent that you want run simulation
          for
        </Typography>
      </Box>
      <Box display="flex" flexDirection="column" gap={2}>
        <FormSearchSelectFieldControl
          control={control}
          fieldName="agentType"
          label="Agent type"
          required
          placeholder="Select agent type"
          size="small"
          fullWidth
          sx={{
            "& .MuiInputLabel-root": {
              fontWeight: 500,
            },
          }}
          options={agentTypeOptions}
        />
        <FormTextFieldV2
          control={control}
          fieldName="agentName"
          label="Agent name"
          required
          placeholder="Give your agent a clear name"
          size="small"
          fullWidth
          sx={{
            "& .MuiInputLabel-root": {
              fontWeight: 500,
            },
          }}
        />
        <LanguageMultiSelect
          control={control}
          fieldName="languages"
          label="Select language"
          placeholder="Select language"
          size="small"
          required
          options={languageOptions.map((option) => ({
            label: option.label,
            id: option.value,
          }))}
          fullWidth
          dropDownMaxHeight={250}
          helperText={
            <Typography
              typography={"s2"}
              color={"text.primary"}
              fontWeight={"fontWeightRegular"}
            >
              Select one or more languages your agent can understand and respond
              in
            </Typography>
          }
        />
      </Box>
    </Box>
  );
};

AgentBasicInfo.propTypes = {
  control: PropTypes.object,
};

export default AgentBasicInfo;
