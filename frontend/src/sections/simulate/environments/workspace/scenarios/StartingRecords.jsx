import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { useStartingRecords } from "src/api/simulate-environments/startingRecords";
import {
  STARTING_RECORDS_COPY as COPY,
  seededValueText,
} from "./startingRecordsUtils";

/** The scenario editor's starting records, read-only: the seeded rows its facts name, as the server reads them. */
export default function StartingRecords({ envId, scenarioKey }) {
  const { data, isLoading, isError, refetch } = useStartingRecords(
    envId,
    scenarioKey,
  );

  const body = () => {
    if (isLoading) return <ContentSkeleton rows={2} label={COPY.loading} />;
    if (isError) return <ErrorState title={COPY.loadError} onRetry={refetch} />;
    if (data.rows_unavailable)
      return (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {data.rows_unavailable.reason}
        </Typography>
      );
    if (!data.records.length)
      return (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {COPY.none}
        </Typography>
      );
    return data.records.map((record) => (
      <Stack
        key={record.label}
        spacing={1}
        data-testid={`starting-record-${record.label}`}
        sx={{
          p: 1.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`${record.label} · ${record.table}`}
        </Typography>
        {record.status === "missing" ? (
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {record.note}
          </Typography>
        ) : (
          <>
            <Typography sx={{ typography: "s1", color: "text.primary" }}>
              {record.summary}
            </Typography>
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
              {Object.entries(record.fields).map(([field, value]) => (
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
                    {seededValueText(value)}
                  </Typography>
                </Box>
              ))}
            </Box>
          </>
        )}
      </Stack>
    ));
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
      {data?.setup_note && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {data.setup_note}
        </Typography>
      )}
    </Stack>
  );
}

StartingRecords.propTypes = {
  envId: PropTypes.string.isRequired,
  // The scenario's key, as the server names it.
  scenarioKey: PropTypes.string.isRequired,
};
