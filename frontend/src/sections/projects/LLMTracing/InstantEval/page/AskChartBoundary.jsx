import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import logger from "src/utils/logger";

/**
 * Keeps one chart's failure to that chart: the run's other charts and its
 * rows still show. It tries again when the run's counts change.
 */
export default class AskChartBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failedAt: null };
  }

  static getDerivedStateFromError() {
    return { failedAt: Symbol("failed") };
  }

  componentDidCatch(error, info) {
    logger.error(`[Ask page] ${this.props.name} failed to draw`, error, info);
  }

  componentDidUpdate(previous) {
    if (this.state.failedAt && previous.resetKey !== this.props.resetKey)
      // A new summary may draw where the last one failed.
      // eslint-disable-next-line react/no-did-update-set-state
      this.setState({ failedAt: null });
  }

  render() {
    if (!this.state.failedAt) return this.props.children;
    return (
      <Box
        role="alert"
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          px: 2,
          py: 1.5,
          display: "flex",
          alignItems: "center",
          gap: 1.5,
        }}
      >
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
          The {this.props.name.toLowerCase()} couldn&apos;t be drawn for this
          run.
        </Typography>
        <Button size="small" onClick={() => this.setState({ failedAt: null })}>
          Try again
        </Button>
      </Box>
    );
  }
}

AskChartBoundary.propTypes = {
  name: PropTypes.string.isRequired,
  resetKey: PropTypes.any,
  children: PropTypes.node,
};
