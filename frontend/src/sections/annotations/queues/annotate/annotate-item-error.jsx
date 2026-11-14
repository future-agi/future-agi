import PropTypes from "prop-types";
import { Alert, Box, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

export default function AnnotateItemError({ statusCode, onBack, onRetry }) {
  const forbidden = statusCode === 403;
  const missing = statusCode === 404;
  const title = forbidden
    ? "Item access denied"
    : missing
      ? "Item not found"
      : "Unable to load item";
  const message = forbidden
    ? "You don't have access to this item. It may be assigned to another annotator. Return to the queue or ask a queue manager to check your assignment."
    : missing
      ? "This item was deleted or is no longer available in this queue. Return to the queue to choose another item."
      : "The item could not be loaded. Try again or return to the queue.";

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "80vh",
        gap: 2,
      }}
    >
      <Iconify icon="mingcute:warning-line" width={64} color="warning.main" />
      <Typography variant="h5">{title}</Typography>
      <Alert severity="warning" sx={{ maxWidth: 480 }}>
        {message}
      </Alert>
      {!forbidden && !missing && (
        <Button variant="outlined" onClick={onRetry}>Try Again</Button>
      )}
      <Button variant="outlined" onClick={onBack}>Back to Queue</Button>
    </Box>
  );
}

AnnotateItemError.propTypes = {
  statusCode: PropTypes.number,
  onBack: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
};
