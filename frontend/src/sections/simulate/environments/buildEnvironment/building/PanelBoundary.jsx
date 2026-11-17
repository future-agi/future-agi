import PropTypes from "prop-types";
import { Component as ReactComponent } from "react";
import { Box, Typography, Button } from "@mui/material";
import logger from "src/utils/logger";

const PANEL_ERROR_COPY = {
  title: "This panel couldn't load",
  body: "The chat on the left still works. Reload the panel, or refresh the page if it keeps happening.",
  reload: "Reload panel",
};

/**
 * Component-scoped error boundary around the derivation panels. If
 * something in there throws — a shape mismatch, a bad ref, whatever
 * — we show a short recoverable message inline instead of nuking the
 * whole app behind the top-level "Houston" page, and send the error to
 * the error reporter. The user keeps working on the left-hand chat.
 */
export default class PanelBoundary extends ReactComponent {
  constructor(props) { super(props); this.state = { err: null }; }

  static getDerivedStateFromError(err) { return { err }; }

  componentDidCatch(err, info) {
    logger.error("[PanelBoundary] Rendering error caught", err, {
      componentStack: info?.componentStack,
    });
  }

  render() {
    const { err } = this.state;
    const { children } = this.props;
    if (err) {
      return (
        <Box sx={{ p: 3, m: 2, borderRadius: "8px", bgcolor: "status.danger.bg" }}>
          <Typography sx={{ typography: "s1", fontWeight: 500, color: "status.danger.fg", mb: 1 }}>
            {PANEL_ERROR_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary", mb: 1.5 }}>
            {PANEL_ERROR_COPY.body}
          </Typography>
          <Button
            variant="text" size="small"
            onClick={() => this.setState({ err: null })}
          >
            {PANEL_ERROR_COPY.reload}
          </Button>
        </Box>
      );
    }
    return children;
  }
}

PanelBoundary.propTypes = { children: PropTypes.node };
