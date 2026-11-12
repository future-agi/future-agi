import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography, useTheme } from "@mui/material";
import { statusColors } from "src/utils/runStatus";

/**
 * Screenshot with click position overlays.
 * Shows numbered markers where the CUA agent clicked.
 *
 * Config:
 *   - screenshotUrl: URL or base64 of the screenshot
 *   - clicks: [{ x, y, label?, action?, step?, status? }]
 *   - imageWidth: original image width (for coordinate scaling)
 *   - imageHeight: original image height
 *
 * DataBinding:
 *   - screenshotFromSpan: { urlPath, coordinatePath }
 *   - clicksFromSpans: { xPath, yPath, labelPath?, actionPath? }
 */

// Marker status -> role in the one status map; no status stays brand.
const STATUS_ROLE = {
  OK: "success",
  ERROR: "danger",
  ok: "success",
  error: "danger",
};

function ClickMarker({ x, y, cx, cy, step, label, action, status }) {
  const theme = useTheme();
  const color = STATUS_ROLE[status]
    ? statusColors(theme, STATUS_ROLE[status]).solid
    : theme.palette.primary.main;

  const [hovered, setHovered] = useState(false);

  return (
    <g
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{ cursor: "pointer" }}
    >
      {/* Pulse ring */}
      <circle cx={cx} cy={cy} r={16} fill={color} opacity={0.15} />
      {/* Main circle */}
      <circle
        cx={cx}
        cy={cy}
        r={10}
        fill={color}
        stroke={theme.palette.common.white}
        strokeWidth={2}
      />
      {/* Step number */}
      <text
        x={cx}
        y={cy + 1}
        textAnchor="middle"
        dominantBaseline="middle"
        fill={theme.palette.common.white}
        fontSize={11}
        fontWeight={600}
        fontFamily="Inter, sans-serif"
      >
        {step ?? "•"}
      </text>

      {/* Tooltip on hover */}
      {hovered && (
        <g>
          <rect
            x={cx + 14}
            y={cy - 16}
            width={Math.max(120, (label || action || "").length * 7)}
            height={32}
            rx={4}
            fill="rgba(0,0,0,0.85)"
          />
          <text
            x={cx + 20}
            y={cy - 4}
            fill={theme.palette.common.white}
            fontSize={11}
            fontFamily="Inter, sans-serif"
          >
            {label || action || `Click (${Math.round(x)}, ${Math.round(y)})`}
          </text>
          <text
            x={cx + 20}
            y={cy + 10}
            fill={theme.palette.grey[400]}
            fontSize={11}
            fontFamily="Inter, sans-serif"
          >
            Step {step} · ({Math.round(x)}, {Math.round(y)})
            {status === "ERROR" ? " · Failed" : ""}
          </text>
        </g>
      )}
    </g>
  );
}

ClickMarker.propTypes = {
  x: PropTypes.number.isRequired,
  y: PropTypes.number.isRequired,
  cx: PropTypes.number.isRequired,
  cy: PropTypes.number.isRequired,
  step: PropTypes.number,
  label: PropTypes.string,
  action: PropTypes.string,
  status: PropTypes.string,
};

export default function WidgetScreenshot({ config }) {
  const { screenshotUrl, clicks = [], imageWidth, imageHeight } = config;

  const containerRef = React.useRef(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [loadedImage, setLoadedImage] = useState(null);

  // Track container size for coordinate scaling
  React.useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDimensions({ width, height });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  if (!screenshotUrl && !clicks.length) {
    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography variant="body2" color="text.disabled" fontSize={12}>
          No screenshot data available
        </Typography>
      </Box>
    );
  }

  // Natural dimensions belong only to the URL that finished loading.
  const imageDimensions =
    loadedImage && loadedImage.url === screenshotUrl
      ? loadedImage
      : { width: imageWidth, height: imageHeight };
  const hasImageDimensions =
    imageDimensions.width > 0 && imageDimensions.height > 0;
  // Match the image's centered object-fit:contain transform for every overlay.
  const scale = hasImageDimensions
    ? Math.min(
        dimensions.width / imageDimensions.width,
        dimensions.height / imageDimensions.height,
      )
    : 1;
  const offsetX = hasImageDimensions
    ? (dimensions.width - imageDimensions.width * scale) / 2
    : 0;
  const offsetY = hasImageDimensions
    ? (dimensions.height - imageDimensions.height * scale) / 2
    : 0;
  const positionedClicks = clicks.map((click) => ({
    ...click,
    cx: click.x * scale + offsetX,
    cy: click.y * scale + offsetY,
  }));

  return (
    <Box
      ref={containerRef}
      sx={{
        position: "relative",
        height: "100%",
        overflow: "hidden",
        bgcolor: "grey.900",
      }}
    >
      {/* Screenshot image */}
      {screenshotUrl && (
        <Box
          component="img"
          src={screenshotUrl}
          alt="Agent screenshot"
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            if (naturalWidth > 0 && naturalHeight > 0) {
              setLoadedImage({
                url: screenshotUrl,
                width: naturalWidth,
                height: naturalHeight,
              });
            }
          }}
          sx={{
            width: "100%",
            height: "100%",
            objectFit: "contain",
            display: "block",
          }}
        />
      )}

      {/* Click overlay */}
      {clicks.length > 0 && dimensions.width > 0 && (
        <svg
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            pointerEvents: "none",
          }}
          viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
        >
          {/* Connection lines between clicks */}
          {positionedClicks.length > 1 &&
            positionedClicks.slice(1).map((click, i) => {
              const prev = positionedClicks[i];
              return (
                <line
                  key={`line-${i}`}
                  x1={prev.cx}
                  y1={prev.cy}
                  x2={click.cx}
                  y2={click.cy}
                  stroke="rgba(123,86,219,0.3)"
                  strokeWidth={1.5}
                  strokeDasharray="4 3"
                />
              );
            })}

          {/* Click markers */}
          {positionedClicks.map((click, i) => (
            <ClickMarker
              key={click.id || i}
              x={click.x}
              y={click.y}
              cx={click.cx}
              cy={click.cy}
              step={click.step ?? i + 1}
              label={click.label}
              action={click.action}
              status={click.status}
            />
          ))}
        </svg>
      )}

      {/* Click count badge */}
      {clicks.length > 0 && (
        <Box
          sx={{
            position: "absolute",
            top: 8,
            right: 8,
            bgcolor: "rgba(0,0,0,0.7)",
            color: "common.white",
            px: 1,
            py: 0.25,
            borderRadius: "4px",
            fontSize: 11,
            fontWeight: 500,
          }}
        >
          {clicks.length} click{clicks.length !== 1 ? "s" : ""}
          {clicks.some((c) => c.status === "ERROR") && (
            <Box
              component="span"
              sx={{ color: (t) => statusColors(t, "danger").fg, ml: 0.5 }}
            >
              · {clicks.filter((c) => c.status === "ERROR").length} failed
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}

WidgetScreenshot.propTypes = { config: PropTypes.object.isRequired };
