import React from "react";
import {
  Box,
  Grid,
  Select,
  Stack,
  Tab,
  Tabs,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import ColumnChart from "src/components/charts/column-chart";
import MultiLineChart from "src/components/charts/multi-line-chart";

import DriftBreakdownTable from "./drift-breakdown-table";

export default function DriftCharts({ title = "Drift over time" }) {
  return (
    <>
      <Box sx={{ my: 2, mx: 2 }}>
        <Grid container>
          <Grid item xs={6}>
            <Stack direction={"row"} spacing={1}>
              <Typography gutterBottom variant="h4">
                {title}
              </Typography>
              <Select size="small" />
            </Stack>
          </Grid>
          <Grid
            item
            xs={6}
            style={{ justifyContent: "flex-end", display: "flex" }}
          >
            <ToggleButtonGroup
              exclusive
              // value={alignment}
              // onChange={handleChange}
            >
              <ToggleButton value="hour">Hour</ToggleButton>
              <ToggleButton value="day">Day</ToggleButton>
              <ToggleButton value="week">Week</ToggleButton>
              <ToggleButton value="month">Month</ToggleButton>
            </ToggleButtonGroup>
          </Grid>

          <Grid item xs={12}>
            <MultiLineChart />
          </Grid>

          <Grid item xs={12}>
            <MultiLineChart />
          </Grid>

          <Grid item xs={12}>
            <Typography gutterBottom variant="h4">
              Distribution
            </Typography>
            <ColumnChart />
          </Grid>

          <Grid item xs={12}>
            <Section variant="panel">
              <Stack direction={"column"} spacing={2}>
                <Typography gutterBottom variant="h4">
                  Drift Breakdown
                </Typography>

                <Tabs>
                  <Tab label="Features" value="features" />
                  <Tab label="Tags" value="tags" />
                </Tabs>
                <DriftBreakdownTable />
              </Stack>
            </Section>
          </Grid>
        </Grid>
      </Box>
    </>
  );
}

DriftCharts.propTypes = {
  title: PropTypes.string,
};
