import PropTypes from "prop-types";
import { Component as ReactComponent } from "react";
import { Box, Typography, Button } from "@mui/material";

/**
 * Component-scoped error boundary around the derivation panels. If
 * something in there throws — a shape mismatch, a bad ref, whatever
 * — we render the error inline instead of nuking the whole app
 * behind the top-level "Houston" page. Users can copy the message
 * and keep working on the left-hand chat.
 */
export default class PanelBoundary extends ReactComponent {
  constructor(props) { super(props); this.state = { err: null }; }

  static getDerivedStateFromError(err) { return { err }; }

  componentDidCatch(err, info) {
    // eslint-disable-next-line no-console
    console.error("[PanelBoundary] caught:", err, info?.componentStack);
  }

  render() {
    const { err } = this.state;
    const { children } = this.props;
    if (err) {
      return (
        <Box sx={{ p: 3, m: 2, borderRadius: "8px", bgcolor: "status.danger.bg" }}>
          <Typography sx={{ typography: "s1", fontWeight: 500, color: "status.danger.fg", mb: 1 }}>
            The right-side panel crashed
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary", mb: 1.5 }}>
            The chat on the left still works. Copy the message below and paste it back to me.
          </Typography>
          <Typography component="pre" sx={{
            typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
            p: 1.5, borderRadius: "4px", bgcolor: "background.neutral",
            whiteSpace: "pre-wrap", wordBreak: "break-word", color: "text.primary",
          }}>
            {String(err?.message || err)}
            {"\n\n"}
            {String(err?.stack || "").split("\n").slice(0, 5).join("\n")}
          </Typography>
          <Button
            variant="text" size="small"
            onClick={() => this.setState({ err: null })}
            sx={{ mt: 1 }}
          >
            Retry
          </Button>
        </Box>
      );
    }
    return children;
  }
}

PanelBoundary.propTypes = { children: PropTypes.node };
