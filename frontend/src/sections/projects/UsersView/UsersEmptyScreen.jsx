import { Box, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React from "react";
import InstructionCodeCopy from "src/sections/project/NewProject/InstructionCodeCopy";
import InstructionTitle from "src/sections/project/NewProject/InstructionTitle";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";

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
      <InstructionTitle
        title="Install Dependencies"
        description="For more instructions, checkout our "
        url="https://docs.futureagi.com/docs/observe/features/users"
        urltext="Docs"
      />

      <InstructionTitle
        title="Setup Telemetry"
        description="Configure your application to send user ID's to Future AGI"
      />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <Box sx={{ mb: 1.5 }}>
          <SegmentedTabs value="JSON">
            <Tab key="telemetry-JSON" label="JSON" value="JSON" />
          </SegmentedTabs>
        </Box>

        <InstructionCodeCopy text={metricsData} language="typescript" />
      </Box>
    </Box>
  );
};

export default UsersEmptyScreen;
