import PropTypes from "prop-types";
import { Autocomplete, TextField } from "@mui/material";
import { ACTORS_COPY } from "./actors.constants";

const F = ACTORS_COPY.worldActors.form;
const recordLabel = (one) => `${one.store}.${one.collection}`;

export default function ActorRecordsField({
  records,
  sources,
  problem,
  onChange,
}) {
  return (
    <Autocomplete
      multiple
      disableCloseOnSelect
      size="small"
      options={sources}
      value={records}
      onChange={(_, next) => onChange(next)}
      getOptionLabel={recordLabel}
      isOptionEqualToValue={(one, other) =>
        recordLabel(one) === recordLabel(other)
      }
      renderInput={(params) => (
        <TextField
          {...params}
          label={F.recordsRead}
          error={Boolean(problem)}
          helperText={problem || (sources.length ? F.recordsHelp : F.noRecords)}
        />
      )}
    />
  );
}

const RECORD = PropTypes.shape({
  store: PropTypes.string,
  collection: PropTypes.string,
});

ActorRecordsField.propTypes = {
  records: PropTypes.arrayOf(RECORD).isRequired,
  // The world's record collections an actor may be given.
  sources: PropTypes.arrayOf(RECORD).isRequired,
  problem: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};
