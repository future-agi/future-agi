import PropTypes from "prop-types";
import { Alert, AlertTitle, Box, Chip } from "@mui/material";

export const PARTIAL_INPUT_WARNING_TYPE = "partial_input";

const DEFAULT_MESSAGE =
  "Eval ran with some inputs empty. Result may be less reliable. Ignore if this is intentional.";

const PartialInputWarningDetails = ({ warnings }) => {
  const partial = warnings?.find(
    (warning) => warning?.type === PARTIAL_INPUT_WARNING_TYPE,
  );
  if (!partial) return null;

  const emptyKeys = partial.empty_keys || [];
  const filledKeys = partial.filled_keys || [];

  return (
    <Alert severity="warning" sx={{ mt: 1.5 }}>
      <AlertTitle>Partial input warning</AlertTitle>
      {partial.message || DEFAULT_MESSAGE}
      {emptyKeys.length > 0 && (
        <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mt: 1 }}>
          {emptyKeys.map((key) => (
            <Chip
              key={key}
              label={`Missing: ${key}`}
              size="small"
              color="warning"
              variant="outlined"
            />
          ))}
        </Box>
      )}
      {filledKeys.length > 0 && (
        <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mt: 0.75 }}>
          {filledKeys.map((key) => (
            <Chip
              key={key}
              label={`Present: ${key}`}
              size="small"
              variant="outlined"
            />
          ))}
        </Box>
      )}
    </Alert>
  );
};

PartialInputWarningDetails.propTypes = {
  warnings: PropTypes.arrayOf(PropTypes.object),
};

export default PartialInputWarningDetails;
