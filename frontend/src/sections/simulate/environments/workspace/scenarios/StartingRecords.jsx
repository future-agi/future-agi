import PropTypes from "prop-types";
import { Box, MenuItem, Stack, TextField, Typography } from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import {
  useChooseStartingRecord,
  useStartingRecords,
} from "src/api/simulate-environments/startingRecords";

const COPY = {
  heading: "Starting records",
  hint: "The seeded rows this scenario starts from. Every run starts from this snapshot, and choosing another row re-proves the scenario.",
  none: "This scenario names no seeded record, so it starts from the world as seeded.",
  readOnly: "This environment was built before records could be chosen here.",
  loadError: "Couldn't load the starting records",
  saving: "Saving to your unsaved changes…",
};

const rowSummary = (keyField, row) =>
  [
    row.key,
    ...Object.entries(row.fields || {})
      .filter(
        ([field, value]) =>
          field !== keyField && value !== null && typeof value !== "object",
      )
      .slice(0, 2)
      .map(([, value]) => String(value)),
  ].join(" · ");

const fieldText = (value) =>
  value !== null && typeof value === "object"
    ? JSON.stringify(value)
    : String(value ?? "—");

export default function StartingRecords({ envId, scenarioKey }) {
  const { data, isLoading, isError, refetch } = useStartingRecords(
    envId,
    scenarioKey,
  );
  const choose = useChooseStartingRecord(envId, scenarioKey);
  const failed = choose.error;
  const problemFor = (label) => {
    if (!failed || choose.variables?.label !== label) return "";
    const own = (failed.problems || []).map((one) => one.message).join(" ");
    return own || failed.message || "";
  };

  const body = () => {
    if (isLoading)
      return <ContentSkeleton rows={2} label="Loading starting records" />;
    if (isError) return <ErrorState title={COPY.loadError} onRetry={refetch} />;
    if (data?.rows_unavailable)
      return (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {data.rows_unavailable.reason}
        </Typography>
      );
    if (!data?.records?.length)
      return (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {COPY.none}
        </Typography>
      );
    return data.records.map((record) => {
      const problem = problemFor(record.label);
      const current = { key: record.key, fields: record.fields };
      return (
        <Stack
          key={record.label}
          spacing={1}
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <TextField
            select
            size="small"
            label={`${record.label} · ${record.table}`}
            value={String(record.key)}
            disabled={!data.editable || choose.isPending}
            error={Boolean(problem)}
            helperText={
              problem ||
              (choose.isPending && choose.variables?.label === record.label
                ? COPY.saving
                : "") ||
              (data.editable ? "" : COPY.readOnly)
            }
            onChange={(event) => {
              if (event.target.value === String(record.key)) return;
              choose.mutate({
                label: record.label,
                path: record.path,
                table: record.table,
                key: event.target.value,
              });
            }}
          >
            {[current, ...record.alternatives].map((row) => (
              <MenuItem key={String(row.key)} value={String(row.key)}>
                <Typography sx={{ typography: "s1" }}>
                  {rowSummary(record.key_field, row)}
                </Typography>
              </MenuItem>
            ))}
          </TextField>
          {record.alternatives_total > record.alternatives.length && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`Showing ${record.alternatives.length} of ${record.alternatives_total} other rows in ${record.table}.`}
            </Typography>
          )}
          <Box
            component="dl"
            sx={{
              m: 0,
              display: "grid",
              gridTemplateColumns: "max-content 1fr",
              columnGap: 1.5,
              rowGap: 0.25,
            }}
          >
            {Object.entries(record.fields || {}).map(([field, value]) => (
              <Box key={field} sx={{ display: "contents" }}>
                <Typography
                  component="dt"
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {field}
                </Typography>
                <Typography
                  component="dd"
                  sx={{ typography: "s3", color: "text.secondary", m: 0 }}
                >
                  {fieldText(value)}
                </Typography>
              </Box>
            ))}
          </Box>
        </Stack>
      );
    });
  };

  return (
    <Stack spacing={1.5}>
      <Box>
        <Typography
          sx={{
            typography: "m3",
            fontWeight: "fontWeightMedium",
            color: "text.primary",
          }}
        >
          {COPY.heading}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {COPY.hint}
        </Typography>
      </Box>
      {body()}
    </Stack>
  );
}

StartingRecords.propTypes = {
  envId: PropTypes.string,
  scenarioKey: PropTypes.string,
};
