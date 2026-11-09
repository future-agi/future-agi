/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";

import HubIcon from "./HubIcon";

// A calm, centred state: a bordered tile with one glyph, a title, one line of
// body and at most one action. It never shows sample data.
export function HubState({ icon = "box", title, description, action, compact, testId, tone }) {
  return (
    <Box
      role="status"
      data-testid={testId}
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        px: 2,
        py: compact ? 4 : { xs: 6, sm: 8 },
      }}
    >
      <Box
        sx={{
          maxWidth: 480,
          width: "100%",
          textAlign: "center",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 1.5,
        }}
      >
        <Box
          sx={{
            width: compact ? 40 : 52,
            height: compact ? 40 : 52,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: tone === "error" ? "error.main" : "primary.main",
            bgcolor: "background.paper",
          }}
        >
          <HubIcon name={icon} size={compact ? 18 : 22} />
        </Box>
        <Box>
          <Typography typography="m3" fontWeight="fontWeightSemiBold" color="text.primary">
            {title}
          </Typography>
          {description && (
            <Typography typography="s1" color="text.secondary" sx={{ mt: 0.5 }}>
              {description}
            </Typography>
          )}
        </Box>
        {action}
      </Box>
    </Box>
  );
}

HubState.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node.isRequired,
  description: PropTypes.node,
  action: PropTypes.node,
  compact: PropTypes.bool,
  testId: PropTypes.string,
  tone: PropTypes.oneOf(["error", undefined]),
};

export function HubEmpty(props) {
  return <HubState icon="box" testId="hub-empty" {...props} />;
}

export function HubError({ error, onRetry, title = "This did not load", compact }) {
  const detail = error?.message && !/^Request failed/.test(error.message) ? error.message : null;
  return (
    <HubState
      icon="alert"
      tone="error"
      testId="hub-error"
      compact={compact}
      title={title}
      description={detail || "The Hub registry did not answer. Try again in a moment."}
      action={
        onRetry ? (
          <Button size="small" variant="outlined" onClick={onRetry}>
            Try again
          </Button>
        ) : null
      }
    />
  );
}

HubError.propTypes = {
  error: PropTypes.object,
  onRetry: PropTypes.func,
  title: PropTypes.string,
  compact: PropTypes.bool,
};

// The app's 403 state, sized for a page inside the dashboard.
export function HubForbidden() {
  return (
    <HubState
      icon="lock"
      testId="hub-forbidden"
      title="No permission"
      description="The page you're trying to access has restricted access. The review pages are for Future AGI Hub maintainers."
      action={
        <Button component={RouterLink} href="/dashboard/hub" variant="outlined" size="small">
          Back to the Hub
        </Button>
      }
    />
  );
}

export function HubSkeletonCards({ count = 6 }) {
  return (
    <Box
      data-testid="hub-loading"
      sx={{
        display: "grid",
        gap: 2,
        gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(3, 1fr)" },
      }}
    >
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} variant="rounded" height={156} />
      ))}
    </Box>
  );
}

HubSkeletonCards.propTypes = { count: PropTypes.number };

export function HubSkeletonPage() {
  return (
    <Box data-testid="hub-loading" sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Skeleton variant="rounded" height={72} />
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 320px" } }}>
        <Skeleton variant="rounded" height={360} />
        <Skeleton variant="rounded" height={360} />
      </Box>
    </Box>
  );
}

export function HubSection({ title, action, children, testId, dense }) {
  return (
    <Box
      data-testid={testId}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: dense ? 1.5 : 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
        minWidth: 0,
      }}
    >
      {(title || action) && (
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1 }}>
          <Typography typography="s1" fontWeight="fontWeightSemiBold" color="text.primary">
            {title}
          </Typography>
          {action}
        </Box>
      )}
      {children}
    </Box>
  );
}

HubSection.propTypes = {
  title: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
  dense: PropTypes.bool,
};
