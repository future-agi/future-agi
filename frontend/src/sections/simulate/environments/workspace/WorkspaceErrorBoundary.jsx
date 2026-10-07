import PropTypes from "prop-types";
import { Component as ReactComponent } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { WORKSPACE_COPY } from "./workspace.constants";

/**
 * Route-level error boundary for the environment workspace.
 *
 * A thrown render error (a bad job shape, a null deref, an unexpected response)
 * would otherwise blank the whole page behind the app's top-level fallback. Here
 * we catch it and render a recoverable state: reload to retry, or go back to the
 * environment list. A class component is required — only class boundaries catch
 * render errors — so navigation uses `window.location` rather than router hooks.
 */
export default class WorkspaceErrorBoundary extends ReactComponent {
  constructor(props) {
    super(props);
    this.state = { err: null };
  }

  static getDerivedStateFromError(err) {
    return { err };
  }

  componentDidCatch(err, info) {
    // eslint-disable-next-line no-console
    console.error("[WorkspaceErrorBoundary] caught:", err, info?.componentStack);
  }

  render() {
    const { err } = this.state;
    const { children } = this.props;
    if (!err) return children;

    return (
      <Box sx={{ height: "100%", minHeight: 420, display: "grid", placeItems: "center", p: 3 }}>
        <Stack spacing={1.5} alignItems="center" sx={{ maxWidth: 480, textAlign: "center" }}>
          <Iconify icon="solar:danger-triangle-linear" width={28} sx={{ color: "error.main" }} />
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>
            {WORKSPACE_COPY.crashed.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {WORKSPACE_COPY.crashed.body}
          </Typography>
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" size="small" onClick={() => window.location.reload()}>
              {WORKSPACE_COPY.crashed.reload}
            </Button>
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => {
                window.location.href = paths.dashboard.simulate.environments.root;
              }}
            >
              {WORKSPACE_COPY.notFound.action}
            </Button>
          </Stack>
        </Stack>
      </Box>
    );
  }
}

WorkspaceErrorBoundary.propTypes = { children: PropTypes.node };
