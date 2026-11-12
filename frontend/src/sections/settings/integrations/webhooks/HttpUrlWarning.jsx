import PropTypes from "prop-types";
import { Alert, Chip, Tooltip } from "@mui/material";

export const HTTP_URL_WARNING_MESSAGE =
  "This URL uses HTTP, so deliveries are not encrypted. Webhook payloads can include event data from your organization. Use an HTTPS URL to protect payloads in transit.";

// Only the server's saved scheme decides this, including for masked/member URLs.
// A draft URL or permission to use HTTP is not evidence of an accepted endpoint.
export default function HttpUrlWarning({ urlScheme, badgeLabel }) {
  if (urlScheme !== "http") return null;
  if (badgeLabel) {
    return (
      <Tooltip title={HTTP_URL_WARNING_MESSAGE}>
        <Chip
          size="small"
          color="warning"
          variant="outlined"
          label={badgeLabel}
          tabIndex={0}
          sx={{ ml: 1 }}
        />
      </Tooltip>
    );
  }
  return (
    <Alert severity="warning" sx={{ mt: 1 }}>
      {HTTP_URL_WARNING_MESSAGE}
    </Alert>
  );
}

HttpUrlWarning.propTypes = {
  urlScheme: PropTypes.oneOf(["http", "https"]),
  badgeLabel: PropTypes.string,
};
