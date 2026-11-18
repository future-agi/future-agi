import {
  Button,
  Collapse,
  FormControl,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import { useBoolean } from "src/hooks/use-boolean";
import { indexToLetter } from "src/utils/utils";

const eventSkeleton = {
  eventName: null,
  filters: [],
  isFilled: false,
};

const eventFilterSkeleton = {
  whereCondition: null,
  propertyName: null,
  equalityCondition: null,
  values: null,
  isFilled: false,
};

export default function CollapsibleSelectEvent() {
  const collapsible = useBoolean();

  const [eventFilters, onChangeEventFilters] = useState([]);

  function onAddEvent() {
    if (eventFilters.length == 0) {
      onChangeEventFilters([eventSkeleton]);
    } else {
      if (eventFilters[eventFilters.length - 1].isFilled) {
        onChangeEventFilters((prevValue) => [...prevValue, eventSkeleton]);
      }
    }
  }

  function onAddEventFilter(eventIndex) {
    onChangeEventFilters((prevValue) => {
      const newEventFilters = [...prevValue];
      if (newEventFilters[eventIndex].filters.length == 0) {
        newEventFilters[eventIndex].filters = [eventFilterSkeleton];
      } else if (
        newEventFilters[eventIndex].filters[
          eventFilters[eventIndex].filters.length - 1
        ].isFilled
      ) {
        newEventFilters[eventIndex].filters = [eventFilterSkeleton];
        const newEventFilterSkeleton = eventFilterSkeleton;
        newEventFilterSkeleton.equalityCondition = "is";
        newEventFilters[eventIndex].filters = [
          ...newEventFilters[eventIndex].filters,
          newEventFilterSkeleton,
        ];
      }
      return newEventFilters;
    });
  }

  return (
    <Section
      variant="panel"
      title="Select Event"
      actions={
        <IconButton
          size="small"
          color={collapsible.value ? "inherit" : "default"}
          onClick={collapsible.onToggle}
        >
          <Iconify
            icon={
              collapsible.value
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
          />
        </IconButton>
      }
    >
      <Collapse in={collapsible.value} unmountOnExit>
        <Grid container spacing={2}>
          {eventFilters?.map((event, eventIndex) => (
            <>
              <Grid item xs={1} key={`head${eventIndex}`}>
                <Typography variant="h6">
                  {indexToLetter(eventIndex)}
                </Typography>
              </Grid>
              <Grid item xs={11} key={`body${eventIndex}`}>
                <Stack direction={"column"} spacing={1}>
                  <Stack direction={"row"} spacing={1}>
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

                    <IconButton onClick={() => onAddEventFilter(eventIndex)}>
                      <Iconify icon={"solar:filter-linear"} />
                    </IconButton>

                    <IconButton>
                      <Iconify icon={"solar:trash-bin-trash-linear"} />
                    </IconButton>
                  </Stack>
                  {event.filters.map((eventFilter, eventFilterIndex) => (
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
            </>
          ))}

          <Grid item xs={12}>
            <Button
              // sx={{ width: 120 }}
              variant="outlined"
              startIcon={<Iconify icon="solar:add-linear" />}
              onClick={onAddEvent}
            >
              Add Event
            </Button>
          </Grid>
        </Grid>
      </Collapse>
    </Section>
  );
}

CollapsibleSelectEvent.propTypes = {
  // eventFilters: PropTypes.array,
  onEventFiltersChange: PropTypes.func,
};
