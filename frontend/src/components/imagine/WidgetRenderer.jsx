import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import WIDGET_REGISTRY from "./widgets";

/**
 * Renders a single widget from its config.
 * For dynamicAnalysis widgets:
 *   - Auto-runs analysis on mount when _needsAnalysis is true
 *   - Shows skeleton loader while waiting
 *   - Shows "Rerun" button in title bar when content is available
 */
export default function WidgetRenderer({ widget, traceData, onRunAnalysis }) {
  const needsAnalysis = widget._needsAnalysis;
  const hasDynamicAnalysis = !!widget.dynamicAnalysis;
  const Component = WIDGET_REGISTRY[widget.type];
  const autoRunRef = useRef(false);

  // Auto-run analysis once when widget needs it
  useEffect(() => {
    if (needsAnalysis && !autoRunRef.current) {
      autoRunRef.current = true;
      onRunAnalysis?.(widget);
    }
  }, [needsAnalysis]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset auto-run flag when widget no longer needs analysis (for next trace switch)
  useEffect(() => {
    if (!needsAnalysis) {
      autoRunRef.current = false;
    }
  }, [needsAnalysis]);

  if (!Component) {
    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          color: "text.disabled",
          gap: 1,
        }}
      >
        <Iconify icon="solar:danger-circle-linear" width={24} />
        <Typography fontSize={12}>
          Unknown widget type: {widget.type}
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* Title bar */}
      {(widget.title || hasDynamicAnalysis) && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            px: 1.5,
            py: 0.75,
            flexShrink: 0,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.primary",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: 1,
            }}
          >
            {widget.title}
          </Typography>

          {/* Dynamic analysis status in title bar */}
          {hasDynamicAnalysis &&
            (needsAnalysis ? (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  flexShrink: 0,
                }}
              >
                <Box
                  sx={{
                    width: 12,
                    height: 12,
                    borderRadius: "50%",
                    border: "2px solid",
                    borderColor: "primary.main",
                    borderTopColor: "transparent",
                    animation: "spin 0.8s linear infinite",
                    "@keyframes spin": { to: { transform: "rotate(360deg)" } },
                  }}
                />
                <Typography
                  sx={{ fontSize: 12, color: "primary.main", fontWeight: 500 }}
                >
                  Running...
                </Typography>
              </Box>
            ) : (
              <ButtonBase
                onClick={() => onRunAnalysis?.(widget)}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.25,
                  px: 0.75,
                  py: 0.25,
                  borderRadius: "4px",
                  fontSize: 12,
                  fontWeight: 500,
                  color: "text.secondary",
                  flexShrink: 0,
                  "&:hover": { bgcolor: "action.hover", color: "primary.main" },
                }}
              >
                <Iconify icon="solar:refresh-linear" width={14} />
                Rerun
              </ButtonBase>
            ))}
        </Box>
      )}

      {/* Content area */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {needsAnalysis ? (
          /* Skeleton loader while Falcon is analyzing */
          <Box
            sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1 }}
          >
            {[0.92, 1, 0.75, 0.88, 0.6, 0.95, 0.45].map((w, i) => (
              <Box
                key={i}
                sx={{
                  height: i === 0 ? 14 : 10,
                  width: `${w * 100}%`,
                  borderRadius: "4px",
                  bgcolor: "action.hover",
                  animation: "pulse 1.5s ease-in-out infinite",
                  animationDelay: `${i * 0.1}s`,
                  "@keyframes pulse": {
                    "0%, 100%": { opacity: 0.4 },
                    "50%": { opacity: 0.8 },
                  },
                }}
              />
            ))}
            <Typography
              sx={{
                fontSize: 12,
                color: "text.disabled",
                mt: 0.5,
                fontStyle: "italic",
              }}
            >
              Falcon is analyzing this trace...
            </Typography>
          </Box>
        ) : (
          <Component config={widget.config || {}} traceData={traceData} />
        )}
      </Box>
    </Box>
  );
}

WidgetRenderer.propTypes = {
  widget: PropTypes.shape({
    id: PropTypes.string.isRequired,
    type: PropTypes.string.isRequired,
    title: PropTypes.string,
    config: PropTypes.object,
    _needsAnalysis: PropTypes.bool,
    dynamicAnalysis: PropTypes.object,
  }).isRequired,
  traceData: PropTypes.object,
  onRunAnalysis: PropTypes.func,
};
