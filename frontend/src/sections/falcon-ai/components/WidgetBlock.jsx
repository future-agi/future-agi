import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import WIDGET_REGISTRY from "src/components/imagine/widgets";

// Chart-shaped widgets need an explicit height in the chat column; textual
// widgets size themselves.
const CHART_TYPES = new Set([
  "bar_chart",
  "line_chart",
  "area_chart",
  "pie_chart",
  "donut_chart",
  "heatmap",
  "radar_chart",
  "timeline",
  "agent_graph",
  "span_tree",
]);

/**
 * Chat-side widget card (Phase 4C "widget answers beyond Imagine").
 *
 * Renders a `widget_render` payload inline in the conversation using the
 * same widget components as the Imagine canvas. Outside Imagine there is no
 * trace bound to the view, so only widgets with STATIC `config` data render;
 * a dataBinding-only widget shows a hint to open it on the Imagine canvas
 * (the prompt instructs the model to embed static config in chat).
 */
export default function WidgetBlock({ widget }) {
  const Component = WIDGET_REGISTRY[widget.type];
  const hasStaticConfig =
    widget.config && Object.keys(widget.config).length > 0;
  const bindingOnly = !hasStaticConfig && !!widget.dataBinding;

  let body;
  if (!Component) {
    body = (
      <Fallback icon="mdi:alert-circle-outline">
        Unknown widget type: {widget.type}
      </Fallback>
    );
  } else if (bindingOnly) {
    body = (
      <Fallback icon="mdi:link-variant">
        This widget binds to live trace data — open it on the Imagine canvas
        to see it.
      </Fallback>
    );
  } else {
    body = (
      <Box
        sx={{
          ...(CHART_TYPES.has(widget.type)
            ? { height: 260 }
            : { minHeight: 48 }),
          overflow: "auto",
        }}
      >
        <Component config={widget.config || {}} />
      </Box>
    );
  }

  return (
    <Box
      data-testid="falcon-widget-block"
      sx={{
        my: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {widget.title && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon="mdi:chart-box-outline"
            width={14}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.primary",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {widget.title}
          </Typography>
        </Box>
      )}
      {body}
    </Box>
  );
}

function Fallback({ icon, children }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        py: 1.5,
        color: "text.disabled",
      }}
    >
      <Iconify icon={icon} width={18} sx={{ flexShrink: 0 }} />
      <Typography fontSize={12}>{children}</Typography>
    </Box>
  );
}

Fallback.propTypes = {
  icon: PropTypes.string,
  children: PropTypes.node,
};

WidgetBlock.propTypes = {
  widget: PropTypes.shape({
    id: PropTypes.string,
    type: PropTypes.string.isRequired,
    title: PropTypes.string,
    config: PropTypes.object,
    dataBinding: PropTypes.object,
  }).isRequired,
};
