import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { channelLabel, otherPartyLabel } from "./transcriptUtils";

export default function OtherPartyName({ party, color = "text.primary" }) {
  const tag = channelLabel(party.channel);
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
    >
      <Typography component="span" variant="s3" sx={{ color, fontWeight: 600 }}>
        {otherPartyLabel(party)}
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
