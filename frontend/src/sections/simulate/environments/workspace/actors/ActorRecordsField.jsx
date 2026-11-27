import PropTypes from "prop-types";
import { Autocomplete, TextField } from "@mui/material";
import { useController } from "react-hook-form";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;
const recordLabel = (one) => `${one.store}.${one.collection}`;

export default function ActorRecordsField({ form, sources }) {
  const { field } = useController({ name: "records" });
  const problem = form.problems.records;
  return (
    <Autocomplete
      multiple
      disableCloseOnSelect
      size="small"
      options={sources}
      value={field.value}
      onChange={(_, next) => form.put("records", next)}
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
  form: ACTOR_FORM_SHAPE.isRequired,
  // The world's record collections an actor may be given.
  sources: PropTypes.arrayOf(RECORD).isRequired,
};
