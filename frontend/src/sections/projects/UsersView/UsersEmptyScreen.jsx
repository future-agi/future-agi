import { Box, useTheme } from "@mui/material";
import React, { useMemo } from "react";
import InstructionCodeCopy from "src/sections/project/NewProject/InstructionCodeCopy";
import InstructionTitle from "src/sections/project/NewProject/InstructionTitle";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";
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

  const theme = useTheme();

  const tabWrapperStyles = useMemo(
    () => ({
      marginBottom: 0,
      alignSelf: "flex-start",
    }),
    [],
  );

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
        title="Send user IDs"
        description="Configure your application to attach user IDs to Future AGI traces."
      />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <TabWrapper sx={tabWrapperStyles}>
          <CustomTabs
            textColor="primary"
            value="JSON"
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
                opacity: 0.08,
                height: "100%",
                borderRadius: "8px",
              },
            }}
          >
            <CustomTab
              key="telemetry-JSON"
              label="JSON"
              value="JSON"
              sx={{ color: "primary.dark" }}
            />
          </CustomTabs>
        </TabWrapper>

        <InstructionCodeCopy text={metricsData} language="typescript" />
      </Box>
    </Box>
  );
};

export default UsersEmptyScreen;
