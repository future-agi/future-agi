import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

const labels = {
  disabled: "Attachment capture is disabled",
  unsupported: "Recording source unavailable",
  waiting: "Waiting for recording completion",
  pending_upload: "Uploading recording",
  importing: "Importing recording",
  validating: "Validating recording",
  pending: "Recording is not ready yet",
  loading: "Loading recording",
  failed: "Recording could not be prepared",
  expired: "Recording expired",
  deleted: "Recording deleted",
  rejected: "Recording unavailable",
  not_found: "Recording unavailable",
};

// Do not render server exception messages, filenames or denied object counts.
export default function AttachmentStatus({ status = "not_found" }) {
  return (
    <Stack
      role="status"
      aria-live="polite"
      alignItems="center"
      justifyContent="center"
      sx={{ p: 2, minHeight: 100 }}
    >
      <Typography variant="body2" color="text.secondary">
        {labels[status] || labels.not_found}
      </Typography>
    </Stack>
  );
}
AttachmentStatus.propTypes = { status: PropTypes.string };
