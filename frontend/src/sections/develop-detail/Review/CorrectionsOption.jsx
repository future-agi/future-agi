import React, { useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import { MenuItem, Stack, Switch, TextField, Typography } from "@mui/material";
import { useDatasetColumns } from "src/sections/develop-detail/DataTab/DatapointDrawerV2/itemHooks";
import { expectedColumns } from "./reviewModel";

/**
 * Start review › "Reviewers can correct the expected answer" (W4-3): a
 * switch and the expected-answer column to correct. Each correction is a
 * suggestion to the dataset; the owner picks which ones become a version.
 * Absent when the dataset has no expected-answer column.
 * `value`: `{on, column_id, column_name}`; the queue metadata stores
 * `corrections: {column_id, column_name}` when on.
 */
export default function CorrectionsOption({ datasetId, value, onChange }) {
  const { data: columns } = useDatasetColumns(datasetId);
  const options = useMemo(() => expectedColumns(columns), [columns]);
  const first = options[0];
  useEffect(() => {
    if (!value && first)
      onChange({ on: false, column_id: first.id, column_name: first.name });
  }, [value, first, onChange]);
  if (!options.length || !value) return null;
  const set = (patch) => onChange({ ...value, ...patch });
  return (
    <Stack spacing={0.5} data-testid="corrections-option">
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Switch
          size="small"
          checked={value.on}
          onChange={(e) => set({ on: e.target.checked })}
          inputProps={{
            "aria-label": "Reviewers can correct the expected answer",
          }}
        />
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Reviewers can correct the expected answer
        </Typography>
        {value.on ? (
          <TextField
            select
            size="small"
            value={value.column_id}
            onChange={(e) => {
              const col = options.find((c) => c.id === e.target.value);
              if (col) set({ column_id: col.id, column_name: col.name });
            }}
            inputProps={{ "aria-label": "Expected-answer column" }}
            sx={{ minWidth: 180 }}
          >
            {options.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name}
              </MenuItem>
            ))}
          </TextField>
        ) : null}
      </Stack>
      {value.on ? (
        <Typography typography="s2" color="text.secondary" sx={{ pl: 5 }}>
          Each correction is suggested to the dataset; you choose which ones to
          save as a new version.
        </Typography>
      ) : null}
    </Stack>
  );
}

CorrectionsOption.propTypes = {
  datasetId: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
