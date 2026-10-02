import { Box, LinearProgress, useTheme } from "@mui/material";
import React, { useMemo, useState } from "react";
import { Events, handleOnDocsClicked, trackEvent } from "src/utils/Mixpanel";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import InstructionTitle from "./InstructionTitle";
import InstructionCodeCopy from "./InstructionCodeCopy";
import ObserveInstruments from "./ObserveInstuments";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";

const NewExperiment = () => {
  const theme = useTheme();
  const [languageTab, setLanguageTab] = useState("python");
  const {
    data: keysData,
    isSuccess,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["keys"],
    queryFn: () =>
      axios.get(endpoints.project.getCodeBlockTracer, {
        params: {
          project_type: "experiment",
        },
      }),
    select: (d) => d.data?.result,
  });
  const tabOptions = [
    { label: "Python", value: "python", disabled: false },
    { label: "Typescript", value: "typescript", disabled: false },
  ];

  const tabWrapperStyles = useMemo(
    () => ({
      marginBottom: 0,
      alignSelf: "flex-start",
    }),
    [],
  );

  const cleanCode = (code) => {
    if (typeof code !== "string") return "Code not available";
    return code.replace(/^\n+/, "").replace(/\n+$/, "");
  };

  const getCodeBySection = (section) => {
    const languageKey = languageTab === "python" ? "Python" : "TypeScript";
    return cleanCode(keysData[section]?.[languageKey]);
  };

  if (!isSuccess || !keysData) return <LinearProgress />;

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 4, // 32px spacing between major sections
      }}
    >
      {/* Installation & Keys Section */}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <InstructionTitle
          title="Install Dependencies"
          description="For more instructions, checkout our "
          url="https://docs.futureagi.com/docs/dataset/features/experiments"
          urltext="Docs"
          onUrlClick={() => handleOnDocsClicked("prototype_page")}
        />

        <InstructionTitle description="Configure your application to send traces to Future AGI" />

        {/* Tab selector for installation */}
        <TabWrapper sx={tabWrapperStyles}>
          <CustomTabs
            textColor="primary"
            value={languageTab}
            onChange={(e, value) => setLanguageTab(value)}
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
                opacity: 0.08,
                height: "100%",
                borderRadius: "8px",
              },
            }}
          >
            {tabOptions.map((tab) => (
              <CustomTab
                key={`config-${tab.value}`}
                label={tab.label}
                value={tab.value}
                disabled={tab.disabled}
              />
            ))}
          </CustomTabs>
        </TabWrapper>

        <InstructionCodeCopy
          text={getCodeBySection("installation_guide")}
          language={languageTab}
          // onCopy={() => trackEvent(Events.installDependenciesCopied)}
        />

        {/* API Keys */}
        <Box sx={{ mt: 1.5 }}>
          <InstructionTitle
            title="Load API keys"
            description="load your API keys"
          />
        </Box>

        <InstructionCodeCopy
          text={getCodeBySection("keys")}
          language={languageTab}
          onCopy={() => trackEvent(Events.apikeys)}
        />
      </Box>

      {/* Telemetry Section with its own tab control */}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <InstructionTitle
          title="Setup Telemetry"
          description="Register your application to send traces to this project. The code should be added BEFORE any code execution."
        />

        {/* Separate tab selector for telemetry */}
        <TabWrapper sx={tabWrapperStyles}>
          <CustomTabs
            textColor="primary"
            value={languageTab}
            onChange={(e, value) => setLanguageTab(value)}
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
                opacity: 0.08,
                height: "100%",
                borderRadius: "8px",
              },
            }}
          >
            {tabOptions.map((tab) => (
              <CustomTab
                key={`telemetry-${tab.value}`}
                label={tab.label}
                value={tab.value}
                disabled={tab.disabled}
              />
            ))}
          </CustomTabs>
        </TabWrapper>

        <InstructionCodeCopy
          text={getCodeBySection("project_add_code")}
          language={languageTab}
          // onCopy={() => trackEvent(Events.setupTelemetryCopied)}
        />
      </Box>

      {/* Instruments Section */}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <InstructionTitle
          title="Setup Instrumentation"
          description="Add tracing instrumentation to give you observability into your application."
        />

        <ObserveInstruments
          data={keysData?.instruments}
          isLoading={isLoading}
          isSuccess={isSuccess}
          error={error}
          languageTab={languageTab}
          onLanguageChange={setLanguageTab}
        />
      </Box>
    </Box>
  );
};

export default NewExperiment;
