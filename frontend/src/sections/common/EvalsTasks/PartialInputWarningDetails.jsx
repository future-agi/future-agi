import PropTypes from "prop-types";
import { Box, Chip, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";

import Iconify from "src/components/iconify";

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
    <Box
      sx={(theme) => ({
        mt: 1.5,
        p: 1.25,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: alpha(
          theme.palette.warning.main,
          theme.palette.mode === "dark" ? 0.4 : 0.3,
        ),
        bgcolor: alpha(
          theme.palette.warning.main,
          theme.palette.mode === "dark" ? 0.12 : 0.08,
        ),
      })}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.75 }}>
        <Iconify
          icon="material-symbols:warning-rounded"
          width={14}
          sx={(theme) => ({
            color:
              theme.palette.mode === "dark"
                ? theme.palette.warning.light
                : theme.palette.warning.dark,
            flexShrink: 0,
          })}
        />
        <Typography
          variant="caption"
          fontWeight={600}
          sx={(theme) => ({
            fontSize: "11px",
            color:
              theme.palette.mode === "dark"
                ? theme.palette.warning.light
                : theme.palette.warning.dark,
          })}
        >
          Partial input warning
        </Typography>
      </Box>
      <Typography
        variant="body2"
        sx={{ fontSize: "12px", color: "text.secondary", lineHeight: 1.5 }}
      >
        {partial.message || DEFAULT_MESSAGE}
      </Typography>
      {emptyKeys.length > 0 && (
        <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mt: 1 }}>
          {emptyKeys.map((key) => (
            <Chip
              key={key}
              label={`Missing: ${key}`}
              size="small"
              color="warning"
              variant="outlined"
              sx={{ fontSize: "10px", height: 18 }}
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
              sx={{ fontSize: "10px", height: 18 }}
            />
          ))}
        </Box>
      )}
    </Box>
  );
};

PartialInputWarningDetails.propTypes = {
  warnings: PropTypes.arrayOf(PropTypes.object),
};

export default PartialInputWarningDetails;
