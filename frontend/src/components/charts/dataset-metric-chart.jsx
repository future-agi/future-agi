/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import {
  Box,
  Grid,
  Paper,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import BrushLineChart from "./brush-line-chart";
import BaselineChart from "./baseline-chart";

export default function DatasetMetricChart({
  title = "Primary Dataset",
  isBrush = true,
}) {
  return (
    <>
      <Box sx={{ my: 2, mx: 2 }}>
        <Grid container>
          <Grid item xs={6}>
            <Typography gutterBottom variant="h4">
              {title}
            </Typography>
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

          <Grid item xs={9}>
            {isBrush && <BrushLineChart></BrushLineChart>}

            {!isBrush && <BaselineChart></BaselineChart>}
          </Grid>

          <Grid item xs={3}>
            <Paper
              elevation={0}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                m: "10px",
                padding: "20px",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                // width: '300px', // adjust as needed
                // height: '200px', // adjust as needed
              }}
            >
              <Typography variant="subtitle1" component="div">
                False Negative Rate
              </Typography>
              <Typography variant="caption" component="div" gutterBottom>
                Dec 20 to Dec 21
              </Typography>
              <Typography variant="h4" component="div">
                0.6525
              </Typography>

              {!isBrush && (
                <>
                  <Typography variant="subtitle2" component="div">
                    False Negative Rate
                  </Typography>
                  <Typography variant="caption" component="div" gutterBottom>
                    Dec 20 to Dec 21
                  </Typography>
                  <Typography variant="h6" component="div">
                    0.6525
                  </Typography>
                </>
              )}
            </Paper>
          </Grid>
        </Grid>
      </Box>
    </>
  );
}

DatasetMetricChart.propTypes = {
  title: PropTypes.string,
  isBrush: PropTypes.bool,
};
