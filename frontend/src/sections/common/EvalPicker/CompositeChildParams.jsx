import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";

const CompositeChildParams = ({ params }) => {
  const entries = Object.entries(params || {});
  if (entries.length === 0) return null;

  return (
    <Box
      sx={{
        mt: 0.75,
        pt: 0.75,
        borderTop: "1px dashed",
        borderColor: "divider",
      }}
    >
      <Typography
        variant="s3"
        color="text.secondary"
        sx={{
          fontWeight: "fontWeightSemiBold",
          display: "block",
          mb: 0.25,
        }}
      >
        Parameters
      </Typography>
      {entries.map(([key, value]) => (
        <Box
          key={key}
          sx={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: 1.5,
          }}
        >
          <Typography variant="s3" color="text.secondary" noWrap>
            {key}
          </Typography>
          <Typography
            variant="s3"
            sx={{
              fontFamily: "monospace",
              fontVariantNumeric: "tabular-nums",
              textAlign: "right",
              overflowWrap: "anywhere",
            }}
          >
            {typeof value === "object" ? JSON.stringify(value) : String(value)}
          </Typography>
        </Box>
      ))}
    </Box>
  );
};

CompositeChildParams.propTypes = {
  params: PropTypes.objectOf(
    PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.number,
      PropTypes.bool,
      PropTypes.object,
      PropTypes.array,
    ]),
  ),
};

export default CompositeChildParams;
