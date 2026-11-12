import React, { useEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";

import {
  requestLogQueryParams,
  useOldestRequestTime,
  useRequestVolume,
} from "./hooks/useRequestLogs";
import { volumeSeries, volumeTitle, volumeWindow } from "./requestVolume";
import {
  UTC_DAYS_NOTE,
  bucketTime,
  localTimeAxis,
  localTimeTooltip,
} from "../utils/timeZone";

// Requests over time above the Logs table, as Vercel's logs open: OK and
// error requests stacked per bucket, under every filter the table has
// (search included; GET /agentcc/request-logs/volume/ applies the list's own
// filters), so the strip counts the requests the table lists. With no start
// time the table lists every request, so the strip runs from the oldest
// request that matches the table to now. A failed read is one quiet line,
// never a toast next to the table.

const CHART_HEIGHT = 88;

function LegendItem({ color, label }) {
  return (
    <Box
      component="li"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}
    >
      <Box
        aria-hidden
        sx={{ width: 8, height: 8, borderRadius: 0.5, bgcolor: color }}
      />
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
    </Box>
  );
}

LegendItem.propTypes = {
  color: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
};

export default function RequestVolumeStrip({ filters, now }) {
  const theme = useTheme();
  const { startedAfter, startedBefore } = filters;
  const oldest = useOldestRequestTime(filters, { enabled: !startedAfter });
  const waiting = !startedAfter && oldest.isLoading;
  const none = !startedAfter && !oldest.isLoading && oldest.data === null;
  const range = useMemo(
    () =>
      volumeWindow(
        { startedAfter, startedBefore },
        now == null ? new Date() : new Date(now),
        oldest.data,
      ),
    [startedAfter, startedBefore, oldest.data, now],
  );
  const { data, isLoading, isError, isFetching, dataUpdatedAt } =
    useRequestVolume(
      filters,
      { start: range.start, end: range.end, granularity: range.granularity },
      { meta: { errorHandled: true }, enabled: !waiting && !none },
    );

  // Keep a failed moving-clock read usable, but never retain another filter's
  // data or relabel a previous successful range as the newly requested range.
  const filtersKey = JSON.stringify(requestLogQueryParams(filters));
  const lastRead = useRef(null);
  useEffect(() => {
    if (lastRead.current?.filtersKey !== filtersKey) lastRead.current = null;
    if (data != null && !isError)
      lastRead.current = { filtersKey, data, range, at: dataUpdatedAt };
  }, [filtersKey, data, range, isError, dataUpdatedAt]);
  const retained =
    lastRead.current?.filtersKey === filtersKey &&
    (isLoading || isFetching || isError)
      ? lastRead.current
      : null;
  const displayed = data ?? retained?.data;
  const displayedRange = data != null ? range : retained?.range || range;
  const volume = useMemo(() => volumeSeries(displayed?.series), [displayed]);
  const title = volumeTitle(displayedRange);
  const okColor = theme.palette.info.main;
  const errorColor = theme.palette.error.main;
  const buckets = volume.ok.length;
  // Day buckets read in the zone the API cut them in.
  const { day, utc } = bucketTime(displayedRange.granularity, displayed);

  const options = useMemo(
    () => ({
      chart: {
        type: "bar",
        stacked: true,
        toolbar: { show: false },
        zoom: { enabled: false },
        animations: { enabled: false },
        parentHeightOffset: 0,
        fontFamily: theme.typography.fontFamily,
      },
      colors: [okColor, errorColor],
      plotOptions: {
        bar: {
          columnWidth: "70%",
          borderRadius: buckets <= 60 ? 4 : 0,
          borderRadiusApplication: "end",
          borderRadiusWhenStacked: "last",
        },
      },
      // A 2 px gap in the surface colour between stacked segments.
      stroke: {
        show: true,
        width: 2,
        colors: [theme.palette.background.paper],
      },
      dataLabels: { enabled: false },
      legend: { show: false },
      grid: {
        borderColor: theme.palette.divider,
        xaxis: { lines: { show: false } },
        padding: { left: 4, right: 4, top: -12, bottom: -4 },
      },
      xaxis: localTimeAxis({
        color: theme.palette.text.secondary,
        utc,
        axisBorder: { show: false },
        axisTicks: { show: false },
      }),
      yaxis: {
        min: 0,
        tickAmount: 2,
        labels: {
          formatter: (v) => Math.round(v).toLocaleString(),
          style: { colors: [theme.palette.text.secondary], fontSize: "11px" },
        },
      },
      tooltip: {
        theme: theme.palette.mode,
        shared: true,
        intersect: false,
        x: localTimeTooltip({ day, utc }),
      },
    }),
    [theme, okColor, errorColor, buckets, day, utc],
  );

  let body;
  if (none) {
    body = (
      <Typography typography="s2" color="text.secondary">
        No requests in this window.
      </Typography>
    );
  } else if ((waiting || isLoading) && !displayed) {
    body = <Skeleton variant="rounded" height={CHART_HEIGHT} />;
  } else if ((isError || oldest.isError) && !displayed) {
    body = (
      <Typography typography="s2" color="text.secondary">
        Request volume could not be loaded.
      </Typography>
    );
  } else if (volume.requests === 0) {
    body = (
      <Typography typography="s2" color="text.secondary">
        No requests in this window.
      </Typography>
    );
  } else {
    body = (
      <Box
        role="img"
        aria-label={`${title}: ${volume.requests.toLocaleString()} requests, ${volume.failed.toLocaleString()} errors.`}
      >
        <Chart
          type="bar"
          height={CHART_HEIGHT}
          options={options}
          series={[
            { name: "OK", data: volume.ok },
            { name: "Errors", data: volume.errors },
          ]}
        />
      </Box>
    );
  }

  return (
    <Box
      data-testid="request-volume"
      sx={{
        flexShrink: 0,
        px: 1.5,
        pt: 1,
        pb: 0.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          columnGap: 2,
          rowGap: 0.5,
        }}
      >
        <Typography typography="s2" color="text.primary">
          {title}
          {utc && (
            <Typography component="span" typography="s2" color="text.secondary">
              {` · ${UTC_DAYS_NOTE}`}
            </Typography>
          )}
        </Typography>
        <Box
          component="ul"
          aria-label="Legend"
          sx={{ display: "flex", gap: 1.5, m: 0, p: 0, listStyle: "none" }}
        >
          <LegendItem color={okColor} label="OK" />
          <LegendItem color={errorColor} label="Errors" />
        </Box>
      </Box>
      {isFetching && (
        <Typography role="status" typography="s2" color="text.secondary">
          Updating request volume…
          {displayed
            ? " Showing the last successful window until this read completes."
            : ""}
        </Typography>
      )}
      {(isError || oldest.isError) && displayed && (
        <Typography role="status" typography="s2" color="warning.dark">
          Request volume could not be refreshed. Showing stale data from the
          last successful load.
        </Typography>
      )}
      <Box sx={{ mt: 0.5 }}>{body}</Box>
    </Box>
  );
}

RequestVolumeStrip.propTypes = {
  filters: PropTypes.object.isRequired,
  now: PropTypes.number,
};
