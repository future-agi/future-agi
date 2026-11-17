import PersonalKeySnippet, {
  PersonalKeyControl,
} from "src/sections/keys/view/PersonalKeySnippet";
import { Box, LinearProgress, Tab } from "@mui/material";
import React, { useState } from "react";
import { Events, handleOnDocsClicked, trackEvent } from "src/utils/Mixpanel";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import ErrorState from "src/components/error-state";
import { getRequestErrorMessage } from "src/utils/errorUtils";

import InstructionTitle from "./InstructionTitle";
import InstructionCodeCopy from "./InstructionCodeCopy";
import ObserveInstruments from "./ObserveInstuments";
import CollectorUrlNote from "./CollectorUrlNote";
import { SegmentedTabs } from "src/components/tabs/tabs";

const NewObserve = () => {
  const [languageTab, setLanguageTab] = useState("python");
  const {
    data: keysData,
    isSuccess,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["project-sdk-code", "observe"],
    queryFn: () =>
      axios.get(endpoints.project.getCodeBlockTracer, {
        params: {
          project_type: "observe",
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

  // Helper functions to get the correct code based on active tabs
  const getCodeBySection = (section) => {
    const languageKey = languageTab === "python" ? "Python" : "TypeScript";
    return cleanCode(keysData[section]?.[languageKey]);
  };

  if (error) {
    return (
      <ErrorState
        title="Could not load setup instructions"
        detail={getRequestErrorMessage(
          error,
          "Could not load setup instructions",
        )}
        onRetry={() => refetch()}
      />
    );
  }

  if (!isSuccess || !keysData) return <LinearProgress />;

  // register() puts traces in the project with this name, creating it if new.
  const projectNameSetting =
    languageTab === "python" ? "project_name" : "projectName";

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
          url="https://docs.futureagi.com/docs/observe"
          urltext="Docs"
          onUrlClick={() => handleOnDocsClicked("observe_page")}
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
          description={`Set ${projectNameSetting} to the project you want. Observe creates it when its first trace arrives; reuse a name to send to an existing project. Add this code before the rest of your app runs.`}
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

export default function NewObserveWithKey() {
  return (
    <PersonalKeySnippet>
      <NewObserve />
    </PersonalKeySnippet>
  );
}
