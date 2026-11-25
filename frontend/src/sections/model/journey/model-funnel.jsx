import {
  Box,
  Button,
  FormControl,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { Panel, PanelGroup } from "react-resizable-panels";
import MultiLineChart from "src/components/charts/multi-line-chart";
import VerticalResizeHandle from "src/components/resize/vertical-resize-handler";
import Iconify from "src/components/iconify";
import { indexToLetter } from "src/utils/utils";
import InsightsGraphProps from "./insights-graph-props";
import InsightsDateSelector from "./insights-date-selector";

export default function ModelFunnel() {
  const [selectedItemType, setSelectedItemType] = useState({
    index: null,
    type: null,
    eventsMenuEl: null,
  });

  const [selectedDates] = useState({});

  const defaultInsights = {
    metrics: [
      {
        selectedEvent: undefined,
      },
    ],
    propFilter: [],
    breakdown: [],
  };

  const [currentInsights] = useState(defaultInsights);

  const _handleMenuClose = (item) => {
    const ins = currentInsights;
    ins[selectedItemType.type][selectedItemType.index].selectedEvent = item[0];
    if (selectedItemType.index + 1 === ins[selectedItemType.type].length) {
      ins[selectedItemType.type].push({ selectedEvent: undefined });
    }
    // setCurrentInsights(currentInsights)
    setSelectedItemType((prevItem) => ({
      ...prevItem,
      ...ins,
      eventsMenuEl: null,
    }));
  };

  function handleDateChange() {
    throw new Error("Function not implemented.");
  }

  return (
    <>
      <Grid container spacing={2}>
        <Grid item xs={9}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <InsightsDateSelector
              dates={selectedDates}
              onDateChange={handleDateChange}
            />

            <div>
              <InsightsGraphProps />
            </div>
          </div>
          <Box
            sx={{
              height: "90vh",
            }}
          >
            <PanelGroup direction="vertical">
              <Panel defaultSize={20} minSize={15}>
                <MultiLineChart />
              </Panel>
              <VerticalResizeHandle />
              <Panel defaultSize={80} minSize={15}>
                <Section variant="panel" sx={{ p: 0, overflow: "hidden" }}>
                  <Tabs>
                    <Tab label="Chart Data" />
                    <Tab label="Dataset" />
                  </Tabs>
                </Section>
              </Panel>
            </PanelGroup>
          </Box>
        </Grid>
        <Grid item xs={3}>
          <h3>
            <Button
              // variant="contained"
              sx={{
                width: "100%",
                justifyContent: "flex-start", // Aligns text to the left
                paddingLeft: 2,
                // bgcolor: "purple", // Background color of the button
                // color: "common.white", // Text color
                // textTransform: "none", // Keeps the text as-is, without uppercase
                // borderRadius: 1, // Border radius of the button
              }}
              endIcon={<Iconify icon="solar:add-linear" />}
            >
              Metrics
            </Button>
          </h3>
          {currentInsights.metrics.map((item, index) => (
            <Section
              variant="panel"
              key={index}
              sx={{
                border: "1px solid",
                borderRadius: 2, // You can adjust the border-radius
                p: 2, // Padding inside the box
                display: "flex",
                justifyContent: "space-between", // To separate the button and text
                alignItems: "center", // To align items vertically
                width: "100%", // Adjust the width as needed
              }}
            >
              <Grid container alignItems={"center"}>
                <Grid item xs={1} key={`head${index}`}>
                  <Typography variant="h6">{indexToLetter(index)}</Typography>
                </Grid>
                <Grid item xs={11} key={`body${index}`}>
                  <Stack direction={"column"} spacing={1}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <FormControl sx={{ width: "100%" }} size="small">
                        <InputLabel>Event Name</InputLabel>
                        <Select
                          // value={age}
                          label="Event Name"
                          // onChange={handleChange}
                          autoWidth
                        >
                          <MenuItem value={10}>Ten</MenuItem>
                          <MenuItem value={20}>Twenty</MenuItem>
                          <MenuItem value={30}>Thirty</MenuItem>
                        </Select>
                      </FormControl>

                      <IconButton
                      // onClick={(e) => onAddEventFilter(eventIndex)}
                      >
                        <Iconify icon={"solar:filter-linear"} />
                      </IconButton>

                      <IconButton>
                        <Iconify icon={"solar:trash-bin-trash-linear"} />
                      </IconButton>
                    </div>

                    <FormControl size="small">
                      <InputLabel>Event Name</InputLabel>
                      <Select
                        // value={age}
                        label="Event Name"
                        // onChange={handleChange}
                        autoWidth
                      >
                        <MenuItem value={10}>Ten</MenuItem>
                        <MenuItem value={20}>Twenty</MenuItem>
                        <MenuItem value={30}>Thirty</MenuItem>
                      </Select>
                    </FormControl>

                    {item?.filters?.map((eventFilter, eventFilterIndex) => (
                      <>
                        <Stack
                          key={eventFilterIndex}
                          direction={"row"}
                          spacing={1}
                          sx={{ pl: 5 }}
                          alignItems={"center"}
                        >
                          <Typography variant="subtitle1">where</Typography>

                          {/* <FormControl sx={{ minWidth: 150 }} size="small">
                      <InputLabel >Event Name</InputLabel>
                      <Select
                        // value={age}
                        label="Event Name"
                        // onChange={handleChange}
                        autoWidth
                      >
                        <MenuItem value={10}>Ten</MenuItem>
                        <MenuItem value={20}>Twenty</MenuItem>
                        <MenuItem value={30}>Thirty</MenuItem>
                      </Select>
                    </FormControl> */}

                          <FormControl sx={{ minWidth: 150 }} size="small">
                            <InputLabel>Event Name</InputLabel>
                            <Select
                              // value={age}
                              label="Event Name"
                              // onChange={handleChange}
                              autoWidth
                            >
                              <MenuItem value={10}>Ten</MenuItem>
                              <MenuItem value={20}>Twenty</MenuItem>
                              <MenuItem value={30}>Thirty</MenuItem>
                            </Select>
                          </FormControl>

                          <FormControl sx={{ minWidth: 150 }} size="small">
                            <InputLabel>Event Name</InputLabel>
                            <Select
                              // value={age}
                              label="Event Name"
                              // onChange={handleChange}
                              autoWidth
                            >
                              <MenuItem value={10}>Ten</MenuItem>
                              <MenuItem value={20}>Twenty</MenuItem>
                              <MenuItem value={30}>Thirty</MenuItem>
                            </Select>
                          </FormControl>

                          <FormControl sx={{ minWidth: 150 }} size="small">
                            <InputLabel>Event Name</InputLabel>
                            <Select
                              // value={age}
                              label="Event Name"
                              // onChange={handleChange}
                              autoWidth
                            >
                              <MenuItem value={10}>Ten</MenuItem>
                              <MenuItem value={20}>Twenty</MenuItem>
                              <MenuItem value={30}>Thirty</MenuItem>
                            </Select>
                          </FormControl>

                          <IconButton>
                            <Iconify icon={"solar:trash-bin-trash-linear"} />
                          </IconButton>
                        </Stack>
                      </>
                    ))}
                  </Stack>
                </Grid>
              </Grid>

              {/* <Button
                // variant="contained"
                sx={{
                  width: "100%",
                  justifyContent: "flex-start", // Aligns text to the left
                  paddingLeft: 2,
                }}
                onClick={(event) => handleMenuOpen(event, "metrics", index)}
              >
                {item?.selectedEvent ? item.selectedEvent : "Select Event"}
              </Button>
              <IconButton
                aria-label="delete"
                onClick={(event) =>
                  handleRemoveInsight(index, "metrics", event)
                }
              >
                <Iconify icon="solar:close-linear" width={16} />
              </IconButton> */}
            </Section>
          ))}
        </Grid>
      </Grid>
    </>
  );
}

ModelFunnel.propTypes = {
  modelId: PropTypes.string,
};
