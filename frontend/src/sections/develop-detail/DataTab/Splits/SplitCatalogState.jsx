import PropTypes from "prop-types";
import { Button, LinearProgress, Stack, Typography } from "@mui/material";

/** Catalog reads must not masquerade as an empty list or offer stale choices. */
const SplitCatalogState = ({ isError, isFetching, onRetry }) => (
  <Stack gap={1} sx={{ px: 1, py: 1 }}>
    {isError ? (
      <>
        <Typography role="alert" variant="body2" color="error.main">
          Couldn&apos;t load splits.
        </Typography>
        <Button size="small" onClick={onRetry} disabled={isFetching}>
          {isFetching ? "Retrying…" : "Try again"}
        </Button>
      </>
    ) : (
      <>
        <Typography role="status" variant="body2" color="text.secondary">
          Loading splits…
        </Typography>
        <LinearProgress aria-label="Loading splits" />
      </>
    )}
  </Stack>
);

SplitCatalogState.propTypes = {
  isError: PropTypes.bool,
  isFetching: PropTypes.bool,
  onRetry: PropTypes.func.isRequired,
};

export default SplitCatalogState;
