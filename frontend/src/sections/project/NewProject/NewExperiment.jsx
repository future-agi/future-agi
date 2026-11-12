import PersonalKeySnippet, {
  PersonalKeyControl,
} from "src/sections/keys/view/PersonalKeySnippet";
import { Box, LinearProgress, Tab } from "@mui/material";
import React, { useState } from "react";
import { Events, handleOnDocsClicked, trackEvent } from "src/utils/Mixpanel";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import InstructionTitle from "./InstructionTitle";
import InstructionCodeCopy from "./InstructionCodeCopy";
import ObserveInstruments from "./ObserveInstuments";
import CollectorUrlNote from "./CollectorUrlNote";
import { SegmentedTabs } from "src/components/tabs/tabs";

const NewExperiment = () => {
  const [languageTab, setLanguageTab] = useState("python");
  const {
    data: keysData,
    isSuccess,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["project-sdk-code", "experiment"],
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
          title="Install dependencies"
          description="For more instructions, check out our "
          url="https://docs.futureagi.com/docs/dataset/features/experiments"
          urltext="Docs"
          onUrlClick={() => handleOnDocsClicked("prototype_page")}
        />

        <InstructionTitle description="Configure your application to send traces to Future AGI" />

        {/* Tab selector for installation */}
        <Box sx={{ mb: 1.5 }}>
          <SegmentedTabs
            value={languageTab}
            onChange={(e, value) => setLanguageTab(value)}
          >
            {tabOptions.map((tab) => (
              <Tab
                key={`config-${tab.value}`}
                label={tab.label}
                value={tab.value}
                disabled={tab.disabled}
              />
            ))}
          </SegmentedTabs>
        </Box>

        <InstructionCodeCopy
          text={getCodeBySection("installation_guide")}
          language={languageTab}
          // onCopy={() => trackEvent(Events.installDependenciesCopied)}
        />

        {/* API Keys */}
        <Box sx={{ mt: 1.5 }}>
          <InstructionTitle
            title="Use a personal API key"
            description="Create a key or replace the placeholders with your saved values."
          />
        </Box>

        <PersonalKeyControl />
        <InstructionCodeCopy
          text={getCodeBySection("keys")}
          language={languageTab}
          onCopy={() => trackEvent(Events.apikeys)}
        />
        <CollectorUrlNote missing={Boolean(keysData?.collector_url_missing)} />
      </Box>

      {/* Telemetry Section with its own tab control */}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <InstructionTitle
          title="Set up telemetry"
          description="Register your application to send traces to this project. The code should be added BEFORE any code execution."
        />

        {/* Separate tab selector for telemetry */}
        <Box sx={{ mb: 1.5 }}>
          <SegmentedTabs
            value={languageTab}
            onChange={(e, value) => setLanguageTab(value)}
          >
            {tabOptions.map((tab) => (
              <Tab
                key={`telemetry-${tab.value}`}
                label={tab.label}
                value={tab.value}
                disabled={tab.disabled}
              />
            ))}
          </SegmentedTabs>
        </Box>

        <InstructionCodeCopy
          text={getCodeBySection("project_add_code")}
          language={languageTab}
          // onCopy={() => trackEvent(Events.setupTelemetryCopied)}
        />
      </Box>

      {/* Instruments Section */}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <InstructionTitle
          title="Set up instrumentation"
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

export default function NewExperimentWithKey() {
  return (
    <PersonalKeySnippet>
      <NewExperiment />
    </PersonalKeySnippet>
  );
}
