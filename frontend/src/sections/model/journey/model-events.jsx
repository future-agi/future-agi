import { Button, Grid, Stack, Switch, Typography } from "@mui/material";
import { m } from "framer-motion";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import Iconify from "src/components/iconify";

import EventPropertyPopover from "../event-property-popover";

import EventsCollapsibleTable from "./events-collapsible-table";
import InsightsDateSelector, { getDateRange } from "./insights-date-selector";
import CollapsibleSelectEvent from "./collapsible-select-event";
import { FilterButton } from "src/components/filter";

const defaultColumns = [
  "Event Name",
  "Time",
  "AI Model",
  "Model Input",
  "Model Output",
];
const defaultFilters = {
  search: "",
  columns: defaultColumns,
  dateFilter: getDateRange("today"),
};

export default function ModelEvents({ modelId: _modelId }) {
  const columnsPopover = usePopover();
  const [_filters, setFilters] = useState(defaultFilters);
  const [showOnlyPredictions, setShowOnlyPredictions] = useState(false);
  const [modPropertiesData, setModPropertiesData] = useState([]);
  const [eventFilterRows, setEventFilterRows] = useState([]);
  const eventFilterFields = useMemo(
    () =>
      (modPropertiesData || []).map(({ name, dataType }) => ({
        id: name,
        label: name,
        type: dataType === "number" ? "number" : "text",
      })),
    [modPropertiesData],
  );

  const [eventsTable, setEventsTable] = useState([]);

  const [selectedDates, setSelectedDates] = useState({
    selectedTab: "today",
    dateValues: defaultFilters.dateFilter,
  });

  useEffect(() => {
    setEventsTable([]);
    setFilters((prevValue) => ({
      ...prevValue,
      dateFilter: selectedDates.dateValues,
    }));
  }, [selectedDates]);

  function handleDateChange(dates) {
    setSelectedDates(dates);
  }

  function onPropertiesSelectionChange(newPropData) {
    setModPropertiesData(newPropData);
  }

  const [page, setPage] = useState(1);
  const eventsLoading = false;
  const totalPages = 0;
  const currentPage = 0;

  function loadMoreData() {
    if (currentPage !== totalPages && !eventsLoading) {
      setPage(page + 1);
    }
  }

  const loadMoreButton = <Button onClick={loadMoreData}>Load More</Button>;

  function handleOnlyPredictionsChange() {
    setShowOnlyPredictions(!showOnlyPredictions);
  }

  return (
    <>
      <Grid container spacing={0.5}>
        <Grid item xs={12}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <InsightsDateSelector
              dates={selectedDates}
              onDateChange={handleDateChange}
            />

            <Stack direction="row" spacing={0.5} alignItems="center">
              <Switch
                size=""
                checked={showOnlyPredictions}
                onChange={handleOnlyPredictionsChange}
                inputProps={{ "aria-label": "controlled" }}
              />
              <Typography variant="subtitle2">Only Predictions</Typography>

              <Button
                variant="outlined"
                size="small"
                component={m.button}
                startIcon={<Iconify icon="solar:add-linear" />}
              >
                Create Annotation Task
              </Button>

              <Button
                variant="outlined"
                size="small"
                // component={m.button}
                // whileTap="tap"
                // whileHover="hover"
                // variants={varHover(1.05)}
                startIcon={<Iconify icon="solar:pen-linear" />}
                onClick={columnsPopover.onOpen}
                // sx={{
                //   width: 40,
                //   height: 40,
                //   background: (theme) => alpha(theme.palette.text.disabled, 0.08),
                //   ...(columnsPopover.open && {
                //     background: (theme) =>
                //       `linear-gradient(135deg, ${theme.palette.primary.light} 0%, ${theme.palette.primary.main} 100%)`,
                //   }),
                // }}
              >
                Edit Columns |{" "}
                {modPropertiesData?.filter((value) => value.selected).length}
              </Button>

              <CustomPopover
                open={columnsPopover.open}
                onClose={columnsPopover.onClose}
                sx={{ width: 200, p: 0 }}
              >
                <EventPropertyPopover
                  properties={modPropertiesData}
                  onPropertiesSelectionChange={onPropertiesSelectionChange}
                />
              </CustomPopover>

              {/* <TextField
                // fullWidth
                value={filters.name}
                onChange={handleFilterName}
                placeholder="Search..."
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="solar:magnifier-linear" width={16}
                        sx={{ color: "text.disabled" }}
                      />
                    </InputAdornment>
                  ),
                }}
              /> */}
            </Stack>
          </div>
        </Grid>

        <Grid item xs={12}>
          <CollapsibleSelectEvent />
        </Grid>

        <Grid item xs={12}>
          <FilterButton
            fields={eventFilterFields}
            value={eventFilterRows}
            onChange={(rows) => setEventFilterRows(rows || [])}
          />
        </Grid>

        <Grid item xs={12} />
        <Grid item xs={12}>
          <EventsCollapsibleTable
            tableData={eventsTable}
            properties={modPropertiesData}
            loading={eventsLoading}
            hasMoreData={currentPage !== totalPages}
            handleScrollBottom={loadMoreData}
          />
          {!!totalPages && currentPage !== totalPages && loadMoreButton}
        </Grid>
      </Grid>
    </>
  );
}

ModelEvents.propTypes = {
  modelId: PropTypes.string,
};
