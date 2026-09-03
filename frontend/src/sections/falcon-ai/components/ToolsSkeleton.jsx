import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

/**
 * Placeholder for a connector's tool list while its detail record loads.
 *
 * Both connector surfaces read tools from the detail endpoint while showing a
 * list row that has none, so without this each renders "no tools discovered"
 * mid-flight — blaming the connector for a request still in the air.
 *
 * Mirrors the loaded layout element for element so nothing moves when the real
 * tools arrive. Static copy renders for real rather than as bars; only what
 * depends on the response is a skeleton. Defaults match the Customize pane;
 * the settings page supplies its own heading and passes `title={null}`.
 */
export default function ToolsSkeleton({
  title = "Tool permissions",
  subtitle = "Choose when Falcon is allowed to use these tools.",
  groupHeader = true,
  trailing = "icon",
  rows = 3,
}) {
  return (
    <Box role="status" aria-label="Loading tools">
      {title && (
        <Typography
          typography="s2_1"
          fontWeight="fontWeightSemiBold"
          sx={{ mb: 1.5 }}
        >
          {title}
        </Typography>
      )}
      {subtitle && (
        <Typography typography="s2" sx={{ color: "text.secondary", mb: 2 }}>
          {subtitle}
        </Typography>
      )}

      {/* Group header: label + count on the left, "Always allow" on the right */}
      {groupHeader && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 0.75,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
            <Skeleton variant="circular" width={14} height={14} />
            <Skeleton variant="text" width={88} height={16} />
            <Skeleton variant="rounded" width={18} height={18} />
          </Box>
          <Skeleton variant="rounded" width={74} height={22} />
        </Box>
      )}

      <Box
        sx={{
          borderRadius: "8px",
          border: (t) => `1px solid ${t.palette.divider}`,
          overflow: "hidden",
        }}
      >
        {Array.from({ length: rows }, (_, i) => (
          <Box
            key={i}
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              px: 2,
              py: 1,
              borderBottom:
                i === rows - 1
                  ? "none"
                  : (t) => `1px solid ${t.palette.divider}`,
            }}
          >
            <Box sx={{ flex: 1, mr: 1 }}>
              <Skeleton variant="text" width={132} height={18} />
              <Skeleton variant="text" width="62%" height={13} />
            </Box>
            {trailing === "switch" ? (
              <Skeleton variant="rounded" width={34} height={20} />
            ) : (
              <Skeleton variant="circular" width={18} height={18} />
            )}
          </Box>
        ))}
      </Box>
    </Box>
  );
}

ToolsSkeleton.propTypes = {
  title: PropTypes.string,
  subtitle: PropTypes.string,
  groupHeader: PropTypes.bool,
  trailing: PropTypes.oneOf(["icon", "switch"]),
  rows: PropTypes.number,
};
