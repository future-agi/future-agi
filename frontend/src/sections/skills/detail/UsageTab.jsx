import PropTypes from "prop-types";
import { useState } from "react";
import ReactApexChart from "react-apexcharts";
import {
  Box,
  MenuItem,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";

import MetricStrip from "src/components/metric-strip";
import AdoptionPanel from '../governance/AdoptionPanel';
import { useSkillUsage } from "src/api/skills";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import When from "../components/When";
import TechnicalDetails from "../components/TechnicalDetails";
import Section from "src/components/section";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { chartAxisColors, seriesColor } from "src/theme/data-colors";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { usePreviewState } from "../components/SkillsStatePreview";
import { buildUsageChart } from "./usageChart";

export default function UsageTab({ skill }) {
  const theme = useTheme();
  const preview = usePreviewState();
  const [days, setDays] = useState(30);
  const [view, setView] = useState("chart");
  const query = useSkillUsage(skill.id, days);
  const usage = query.data;
  const empty = preview === "empty" || usage?.total === 0;
  const chart = buildUsageChart(
    usage?.byDay || [],
    skill.latestVersion || 0,
    days,
  );
  const axis = chartAxisColors(theme);
  const options = {
    chart: {
      type: "bar",
      stacked: true,
      toolbar: { show: false },
      animations: { enabled: false },
      foreColor: axis.foreColor,
      background: axis.surface,
    },
    colors: chart.series.map((series) => seriesColor(theme, series.colorIndex)),
    plotOptions: {
      bar: {
        columnWidth: "60%",
        borderRadius: 4,
        borderRadiusApplication: "end",
        borderRadiusWhenStacked: "last",
      },
    },
    stroke: { show: true, width: 2, colors: [axis.surface] },
    dataLabels: { enabled: false },
    grid: { borderColor: axis.gridColor },
    xaxis: {
      type: "datetime",
      labels: {
        rotate: 0,
        hideOverlappingLabels: true,
        datetimeUTC: true,
        datetimeFormatter: {
          year: "yyyy",
          month: "MMM yyyy",
          day: "MMM d",
          hour: "HH:mm",
        },
      },
      axisBorder: { show: false },
      axisTicks: { show: false },
    },
    yaxis: {
      min: 0,
      forceNiceScale: true,
      decimalsInFloat: 0,
      title: { text: "Fetches" },
    },
    legend: {
      show: chart.series.length > 1,
      position: "bottom",
      labels: { colors: axis.foreColor },
    },
    tooltip: {
      shared: true,
      intersect: false,
      theme: theme.palette.mode,
      x: { format: "MMM d, yyyy" },
      y: {
        formatter: (value) => `${value} ${value === 1 ? "fetch" : "fetches"}`,
      },
    },
  };

  const fetchMeaning = <Typography variant="s3" color="text.secondary" component="p" sx={{ m: 0 }}>
    Counts requests that fetched this skill. A fetch doesn&apos;t prove an
    agent used it.
  </Typography>;
  const usageShown = !(preview === "loading" || query.isPending || preview === "error" || query.isError || empty) && Boolean(usage);
  return (
    <Stack spacing={3}>
      <TextField
        select
        size="small"
        label="Time range"
        value={days}
        onChange={(event) => setDays(Number(event.target.value))}
        sx={{ width: 180 }}
      >
        {[7, 30, 90].map((value) => (
          <MenuItem key={value} value={value}>
            Last {value} days
          </MenuItem>
        ))}
      </TextField>
      {preview === "loading" || query.isPending ? (
        <ContentSkeleton variant="panel" label="Loading usage" />
      ) : preview === "error" || query.isError ? (
        <ErrorState
          title="Could not load usage"
          detail="Your skill and versions are unchanged."
          onRetry={query.refetch}
        />
      ) : empty ? (
        <EmptyState
          title="No fetches in this period"
          description="Uses appear when someone resolves, downloads or reads this skill through the API or MCP."
        />
      ) : (
        usage && (
          <>
            <AdoptionPanel skill={skill} days={days} summary={<MetricStrip items={[
              { label: "Fetches", value: usage.total.toLocaleString() },
              { label: "Last fetch", value: <When value={usage.lastFetchedAt} /> },
              { label: "Versions fetched", value: usage.byVersion.filter((item) => item.count > 0).length },
            ]} sx={{ "& > .MuiBox-root > .MuiTypography-root": { typography: "s3" }, "& > .MuiBox-root > .MuiBox-root > .MuiTypography-root": { typography: "m1" } }} />}
              about={fetchMeaning}
              details={<>
            <Section
              title="Fetches by version"
              description="Daily requests, grouped by UTC date."
              actions={
                <SegmentedTabs
                  value={view}
                  onChange={(_event, value) => setView(value)}
                  aria-label="Usage display"
                >
                  <Tab value="chart" label="Chart" />
                  <Tab value="table" label="Table" />
                </SegmentedTabs>
              }
            >
              {view === "chart" ? (
                <>
                  <Box
                    role="img"
                    aria-label={`Daily fetches for the last ${days} days. Choose Table to read exact counts for every version.`}
                    sx={{ minWidth: 0, overflowX: "auto" }}
                  >
                    <Box sx={{ minWidth: days * 10 }}>
                      <ReactApexChart
                        type="bar"
                        height={300}
                        options={options}
                        series={chart.series.map(({ name, data }) => ({
                          name,
                          data: data.map((count, index) => ({
                            x: Date.parse(`${chart.dates[index]}T00:00:00Z`),
                            y: count,
                          })),
                        }))}
                      />
                    </Box>
                  </Box>
                  {chart.grouped && (
                    <Typography variant="s3" color="text.secondary">
                      The seven newest versions are shown separately. Earlier
                      versions are grouped; the table shows each version.
                    </Typography>
                  )}
                </>
              ) : (
                <TableContainer>
                  <Table size="small" aria-label="Daily fetches by version">
                    <TableHead>
                      <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                        <TableCell>Date (UTC)</TableCell>
                        <TableCell>Version</TableCell>
                        <TableCell align="right">Fetches</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {usage.byDay.map((item) => (
                        <TableRow
                          hover
                          key={`${item.day}:${item.version}`}
                          sx={{ height: ROW_HEIGHT.default }}
                        >
                          <TableCell>{item.day}</TableCell>
                          <TableCell>v{item.version}</TableCell>
                          <TableCell align="right">
                            {item.count.toLocaleString()}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}
            </Section>
            <Section title="Consumers">
              <TableContainer>
                <Table size="small" aria-label="Skill consumers">
                  <TableHead>
                    <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                      <TableCell>Consumer</TableCell>
                      <TableCell>Channel</TableCell>
                      <TableCell align="right">Fetches</TableCell>
                      <TableCell>Last fetched</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {usage.consumers.map((item, index) => (
                      <TableRow
                        hover
                        key={`${item.label}:${item.channel}:${index}`}
                        sx={{ height: ROW_HEIGHT.default }}
                      >
                        <TableCell>{item.label}</TableCell>
                        <TableCell>{item.channel}</TableCell>
                        <TableCell align="right">
                          {item.count.toLocaleString()}
                        </TableCell>
                        <TableCell><When value={item.lastAt} /></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Section>
              </>} />
          </>
        )
      )}
      {/* While the adoption panel is showing it carries this caveat inside its own "About this data"; otherwise it stands alone. */}
      {!usageShown && <TechnicalDetails title="About this data">{fetchMeaning}</TechnicalDetails>}
    </Stack>
  );
}
UsageTab.propTypes = { skill: PropTypes.object.isRequired };
