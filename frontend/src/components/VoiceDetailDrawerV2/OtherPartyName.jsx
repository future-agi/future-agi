import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { channelLabel, otherPartyLabel } from "./transcriptUtils";

export default function OtherPartyName({ party, color = "text.primary" }) {
  const tag = channelLabel(party.channel);
  const label = otherPartyLabel(party);
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        minWidth: 0,
        maxWidth: "100%",
      }}
    >
      {/* A role can be a whole sentence; keep it to one line and show it in full on hover. */}
      <Typography
        component="span"
        variant="s3"
        title={label}
        noWrap
        sx={{ color, fontWeight: 600, maxWidth: 320 }}
      >
        {label}
      </Typography>
      {tag && (
        <Typography
          component="span"
          variant="s3"
          sx={{
            px: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            color: "text.secondary",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          {tag}
        </Typography>
      )}
    </Box>
  );
}

OtherPartyName.propTypes = {
  party: PropTypes.shape({
    speakerName: PropTypes.string,
    actorRole: PropTypes.string,
    channel: PropTypes.string,
  }).isRequired,
  color: PropTypes.string,
};
