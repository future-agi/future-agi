import PropTypes from "prop-types";
import { ErrorBoundary } from "react-error-boundary";
import { alpha } from "@mui/material/styles";
import { Box, Typography, Button } from "@mui/material";

import { logger } from "src/utils/logger";
import { BUILD_TONES } from "../buildTones";

/**
 * Component-scoped error boundary around the derivation panels. If something in
 * there throws — a shape mismatch, a bad ref, whatever — the error renders
 * inline instead of nuking the whole app behind the top-level "Houston" page.
 * The chat on the left keeps working.
 *
 * Built on react-error-boundary (the repo's boundary of record) rather than a
 * hand-rolled class: it brings `resetErrorBoundary`, and `onError` goes through
 * `logger` instead of a raw console.error. The fallback shows the message only
 * — a stack trace belongs in the log, not on the user's screen.
 */
function PanelFallback({ error, resetErrorBoundary }) {
  return (
    <Box sx={{ p: 3, m: 2, border: "1px solid", borderColor: alpha(BUILD_TONES.red, 0.4), borderRadius: 1.5,
      bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.08 : 0.04) }}>
      <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold", color: BUILD_TONES.red, mb: 1 }}>
        The right-side panel crashed
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}>
        The chat on the left still works. Copy the message below and paste it back to me.
      </Typography>
      <Typography component="pre" sx={{
        typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
        p: 1.5, borderRadius: 1, bgcolor: "background.neutral",
        whiteSpace: "pre-wrap", wordBreak: "break-word", color: "text.primary",
      }}>
        {String(error?.message || error)}
      </Typography>
      <Button
        variant="text" size="small"
        onClick={resetErrorBoundary}
        sx={{ mt: 1, typography: "s3", fontWeight: "fontWeightBold" }}
      >
        Retry
      </Button>
    </Box>
  );
}

PanelFallback.propTypes = {
  error: PropTypes.object,
  resetErrorBoundary: PropTypes.func,
};

export default function PanelBoundary({ children }) {
  return (
    <ErrorBoundary
      FallbackComponent={PanelFallback}
      onError={(error, info) =>
        logger.error("[PanelBoundary] caught", error, {
          componentStack: info?.componentStack,
        })
      }
    >
      {children}
    </ErrorBoundary>
  );
}

PanelBoundary.propTypes = { children: PropTypes.node };
