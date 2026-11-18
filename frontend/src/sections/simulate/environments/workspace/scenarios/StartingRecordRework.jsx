import PropTypes from "prop-types";
import { useState } from "react";
import { Button, MenuItem, Stack, TextField, Typography } from "@mui/material";

import { useReworkStartingRecord } from "src/api/simulate-environments/startingRecords";
import {
  STARTING_RECORDS_COPY as COPY,
  refusalText,
} from "./startingRecordsUtils";

/** "Start from another record" for one starting record: pick a seeded row, and the builder is asked to rework. */
export default function StartingRecordRework({
  envId,
  scenarioKey,
  record,
  disabled,
}) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const rework = useReworkStartingRecord(envId, scenarioKey);

  if (!record.choices.length)
    return (
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {COPY.noChoices}
      </Typography>
    );
  if (!open)
    return (
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={disabled}
        onClick={() => setOpen(true)}
        sx={{ alignSelf: "flex-start" }}
      >
        {COPY.startFromAnother}
      </Button>
    );
  const ask = () =>
    rework.mutate(
      { path: record.path, table: record.table, key },
      {
        onSuccess: () => {
          setOpen(false);
          setKey("");
        },
      },
    );
  return (
    <Stack spacing={1}>
      <TextField
        select
        size="small"
        label={COPY.choose}
        value={key}
        onChange={(event) => setKey(event.target.value)}
        error={rework.isError}
        helperText={
          rework.isError ? refusalText(rework.error, COPY.askFailed) : ""
        }
      >
        {record.choices.map((row) => (
          <MenuItem key={row.key} value={row.key}>
            <Typography sx={{ typography: "s1" }}>{row.summary}</Typography>
          </MenuItem>
        ))}
      </TextField>
      <Stack direction="row" spacing={1}>
        <Button
          size="small"
          variant="outlined"
          disabled={!key || rework.isPending}
          onClick={ask}
        >
          {COPY.ask}
        </Button>
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            setOpen(false);
            rework.reset();
          }}
        >
          {COPY.cancel}
        </Button>
      </Stack>
    </Stack>
  );
}

StartingRecordRework.propTypes = {
  envId: PropTypes.string.isRequired,
  scenarioKey: PropTypes.string.isRequired,
  record: PropTypes.shape({
    path: PropTypes.arrayOf(PropTypes.string).isRequired,
    table: PropTypes.string.isRequired,
    choices: PropTypes.arrayOf(
      PropTypes.shape({ key: PropTypes.string, summary: PropTypes.string }),
    ).isRequired,
  }).isRequired,
  // While a request is still with the builder, no other is filed.
  disabled: PropTypes.bool,
};
