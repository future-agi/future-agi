import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import useIsTruncated from "src/hooks/useIsTruncated";
import { channelLabel, otherPartyLabel } from "./transcriptUtils";
import { OTHER_PARTY_LABEL_MAX_WIDTH } from "./constants";

export default function OtherPartyName({
  party,
  color = "text.primary",
  fontWeight = "fontWeightSemiBold",
}) {
  const tag = channelLabel(party.channel);
  const label = otherPartyLabel(party);
  const [labelRef, cut] = useIsTruncated(label);
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
      <CustomTooltip show={cut} size="small" arrow title={label}>
        <Typography
          ref={labelRef}
          component="span"
          variant="s3"
          noWrap
          sx={{ color, fontWeight, maxWidth: OTHER_PARTY_LABEL_MAX_WIDTH }}
        >
          {label}
        </Typography>
      </CustomTooltip>
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
  fontWeight: PropTypes.string,
};
