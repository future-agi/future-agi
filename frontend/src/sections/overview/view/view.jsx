import React from "react";
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";
import Container from "@mui/material/Container";
import { useSettingsContext } from "src/components/settings";
import { Button, Grid } from "@mui/material";
import { SeoIllustration } from "src/assets/illustrations";
import { ModelListView } from "src/sections/model/view";
import { useOverviewData } from "src/api/misc/overview";

import AppWidgetSummary from "../app-widget-summary";
import AppWelcome from "../app-welcome";

// ----------------------------------------------------------------------

export default function OverviewView() {
  const theme = useTheme();

  const settings = useSettingsContext();

  const { data } = useOverviewData();

  const navigateToDocs = () => {
    window.location.href = "https://docs.thefuturecompany.ai";
  };

  return (
    <Container maxWidth={settings.themeStretch ? false : "xl"}>
      <Box>
        <Grid container spacing={3}>
          <Grid item xs={12} md={12}>
            <AppWelcome
              title={`Welcome 👋 \n `}
              description="Checkout our docs to get you started."
              img={<SeoIllustration />}
              action={
                <Button
                  variant="contained"
                  color="primary"
                  onClick={navigateToDocs}
                >
                  Docs
                </Button>
              }
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <AppWidgetSummary
              title="Last 24 Hr Volume"
              percent={data?.volume?.change}
              total={data?.volume?.total_count}
              chart={{
                // series: [5, 18, 12, 51, 68, 11, 39, 37, 27, 20],
                series: data?.volume?.volume?.map((val) => val.y),
              }}
            />
          </Grid>

          <Grid item xs={12} md={6}>
            <AppWidgetSummary
              title="Total issues"
              percent={data?.issues?.change}
              total={data?.issues?.total_count}
              chart={{
                colors: [theme.palette.info.light, theme.palette.info.main],
                series: data?.issues?.last_day?.map((val) => val.y),
              }}
            />
          </Grid>

          {/* <Grid item xs={12} md={4}>
            <AppWidgetSummary
              title="New Versions"
              percent={-0.1}
              total={678}
              chart={{
                colors: [
                  theme.palette.warning.light,
                  theme.palette.warning.main,
                ],
                series: [8, 9, 31, 8, 16, 37, 8, 33, 46, 31],
              }}
            />
          </Grid> */}
          <Grid item xs={12}>
            <ModelListView />
          </Grid>
        </Grid>
      </Box>
    </Container>
  );
}
