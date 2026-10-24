import { Container, Tab, Tabs, Typography } from "@mui/material";
import Section from "src/components/section";
import React, { useCallback, useState } from "react";
import { useSettingsContext } from "src/components/settings";
import DataConnectors from "../data-connectors";
import DataImportJobs from "../data-import-jobs";

export default function DataConnectView() {
  const settings = useSettingsContext();

  const [currentTab, setCurrentTab] = useState("connectData");

  const handleChangeTab = useCallback((event, newValue) => {
    setCurrentTab(newValue);
  }, []);

  return (
    <>
      <Container maxWidth={settings.themeStretch ? false : "lg"}>
        <Typography variant="h4">Data Upload & Jobs</Typography>

        <Section
          variant="panel"
          sx={{
            p: 2,
          }}
        >
          <Tabs value={currentTab} onChange={handleChangeTab}>
            <Tab label="Connect Data" value={"connectData"}></Tab>
            <Tab label="Jobs" value={"jobs"}></Tab>
          </Tabs>

          {currentTab === "connectData" && <DataConnectors></DataConnectors>}
          {currentTab === "jobs" && <DataImportJobs></DataImportJobs>}
        </Section>
      </Container>
    </>
  );
}
