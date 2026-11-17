import { Box, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React from "react";
import InstructionCodeCopy from "src/sections/project/NewProject/InstructionCodeCopy";
import InstructionTitle from "src/sections/project/NewProject/InstructionTitle";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";

// Every package the Python snippet below imports (openai, fi_instrumentation,
// traceai_openai), so the snippet runs in a fresh environment.
const INSTALL_COMMAND =
  "pip install fi-instrumentation-otel traceai-openai openai";

const UsersEmptyScreen = () => {
  const { observeId } = useParams();

  const { data: metricsData } = useQuery({
    queryKey: ["get-user-example-code", observeId],
    queryFn: async () => {
      const response = await axios.get(endpoints.project.getUserExampleCode(), {
        params: {
          project_id: observeId,
        },
      });
      return response.data.result;
    },
  });

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        width: "82%",
        mt: 1.5,
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <InstructionTitle
          title="Install dependencies"
          description="Run this command in your terminal. For more instructions, see our "
          url="https://docs.futureagi.com/docs/observe/features/users"
          urltext="Docs"
        />
        <InstructionCodeCopy text={INSTALL_COMMAND} language="bash" />
      </Box>

      <InstructionTitle
        title="Set up telemetry"
        description="Configure your application to send user ID's to Future AGI"
      />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <Box sx={{ mb: 1.5 }}>
          <SegmentedTabs value="Python">
            <Tab key="telemetry-Python" label="Python" value="Python" />
          </SegmentedTabs>
        </Box>

        <InstructionCodeCopy text={metricsData} language="python" />
      </Box>
    </Box>
  );
};

export default UsersEmptyScreen;
