import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

export default function WidgetKeyValue({ config }) {
  const items = config.items || [];

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0,
        height: "100%",
        overflow: "auto",
      }}
    >
      {items.map((item, idx) => (
        <Box
          key={item.key || idx}
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            px: 1.5,
            py: 0.75,
            borderBottom: idx < items.length - 1 ? "1px solid" : "none",
            borderColor: "divider",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Typography
            sx={{
              fontSize: 12,
              color: "text.secondary",
              fontWeight: 500,
              flexShrink: 0,
              mr: 2,
            }}
          >
            {item.key}
          </Typography>
          <Typography
            sx={{
              fontSize: 12,
              color: "text.primary",
              fontWeight: 600,
              textAlign: "right",
              fontFamily: item.mono ? "'IBM Plex Mono', monospace" : "inherit",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {item.value ?? "—"}
          </Typography>
        </Box>
      ))}

      {items.length === 0 && (
        <Box sx={{ p: 2, textAlign: "center" }}>
          <Typography variant="body2" color="text.disabled" fontSize={12}>
            No data
          </Typography>
        </Box>
      )}
    </Box>
  );
}

WidgetKeyValue.propTypes = { config: PropTypes.object.isRequired };
