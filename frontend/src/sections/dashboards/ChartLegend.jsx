/* eslint-disable react/prop-types */
import React, { useRef, useState, useEffect, useCallback } from "react";
import { Box, Tooltip, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";

const MAX_LABEL_CHARS = 18;

export default function ChartLegend({
  items,
  colors,
  onHoverSeries,
  onLeaveSeries,
}) {
  const theme = useTheme();
  const containerRef = useRef(null);
  const itemsRef = useRef(null);
  const [visibleCount, setVisibleCount] = useState(items.length);

  // Measure actual rendered item widths to determine how many fit
  useEffect(() => {
    const el = containerRef.current;
    const itemsEl = itemsRef.current;
    if (!el || !itemsEl) return;
    const measure = () => {
      const containerW = el.offsetWidth;
      const moreChipW = 70;
      const gap = 10;
      const padding = 16;
      const availW = containerW - padding;

      // Measure each rendered item's actual width
      const children = Array.from(itemsEl.children);
      let usedW = 0;
      let fitCount = 0;

      for (let i = 0; i < children.length; i++) {
        const childW = children[i].offsetWidth + gap;
        if (usedW + childW + moreChipW > availW && i < children.length - 1) {
          break;
        }
        if (usedW + childW > availW) {
          break;
        }
        usedW += childW;
        fitCount++;
      }

      setVisibleCount(
        fitCount >= items.length ? items.length : Math.max(1, fitCount),
      );
    };
    // Allow one frame for rendering
    requestAnimationFrame(measure);
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [items.length]);

  const truncate = (str) =>
    str.length > MAX_LABEL_CHARS ? `${str.slice(0, MAX_LABEL_CHARS)}…` : str;

  const visible = items.slice(0, visibleCount);
  const overflow = items.slice(visibleCount);

  const renderItem = useCallback(
    (name, idx, forMeasure) => {
      const color = colors[idx % colors.length];
      const needsTruncation = name.length > MAX_LABEL_CHARS;
      const el = (
        <Box
          key={idx}
          onMouseEnter={forMeasure ? undefined : () => onHoverSeries?.(idx)}
          onMouseLeave={forMeasure ? undefined : () => onLeaveSeries?.()}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
            whiteSpace: "nowrap",
            flexShrink: 0,
            cursor: "default",
            borderRadius: "4px",
            px: "4px",
            py: "2px",
            transition: "background 0.15s",
            "&:hover": forMeasure
              ? {}
              : {
                  bgcolor: "action.hover",
                },
          }}
        >
          <Box
            sx={{
              width: 8,
              height: 8,
              borderRadius: "4px",
              bgcolor: color,
              flexShrink: 0,
            }}
          />
          <Typography
            variant="caption"
            sx={{
              color: theme.palette.text.secondary,
              fontSize: "12px",
              fontWeight: 500,
              lineHeight: 1,
            }}
          >
            {truncate(name)}
          </Typography>
        </Box>
      );
      if (forMeasure) return el;
      return needsTruncation ? (
        <Tooltip key={idx} title={name} arrow placement="top">
          {el}
        </Tooltip>
      ) : (
        el
      );
    },
    [colors, onHoverSeries, onLeaveSeries, theme.palette],
  );

  if (items.length <= 1 && items[0] === "total") return null;

  return (
    <Box
      ref={containerRef}
      sx={{
        position: "relative",
        overflow: "hidden",
        flexShrink: 0,
        minHeight: 24,
      }}
    >
      {/* Hidden measurement row — renders all items to measure widths */}
      <Box
        ref={itemsRef}
        sx={{
          display: "flex",
          flexWrap: "nowrap",
          alignItems: "center",
          gap: "10px",
          px: 1,
          py: "4px",
          position: "absolute",
          visibility: "hidden",
          pointerEvents: "none",
          whiteSpace: "nowrap",
        }}
      >
        {items.map((name, i) => renderItem(name, i, true))}
      </Box>

      {/* Visible row — only shows items that fit */}
      <Box
        sx={{
          display: "flex",
          flexWrap: "nowrap",
          alignItems: "center",
          justifyContent: "center",
          gap: "10px",
          px: 1,
          py: "4px",
          overflow: "hidden",
        }}
      >
        {visible.map((name, i) => renderItem(name, i, false))}
        {overflow.length > 0 && (
          <Tooltip
            classes={{ tooltip: "ds-hovercard" }}
            arrow
            placement="bottom"
            title={
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                  p: 0.5,
                }}
              >
                {overflow.map((name, i) => {
                  const idx = visibleCount + i;
                  const color = colors[idx % colors.length];
                  return (
                    <Box
                      key={idx}
                      onMouseEnter={() => onHoverSeries?.(idx)}
                      onMouseLeave={() => onLeaveSeries?.()}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.75,
                        cursor: "default",
                        borderRadius: "4px",
                        px: "4px",
                        py: "2px",
                        mx: "-4px",
                        transition: "background 0.15s",
                        "&:hover": {
                          bgcolor: "rgba(255,255,255,0.12)",
                        },
                      }}
                    >
                      <Box
                        sx={{
                          width: 8,
                          height: 8,
                          borderRadius: "4px",
                          bgcolor: color,
                          flexShrink: 0,
                        }}
                      />
                      <Typography
                        variant="caption"
                        sx={{ fontSize: "12px", color: "text.primary" }}
                      >
                        {name}
                      </Typography>
                    </Box>
                  );
                })}
              </Box>
            }
          >
            <Box
              sx={{
                display: "inline-flex",
                alignItems: "center",
                px: "6px",
                py: "2px",
                borderRadius: "8px",
                flexShrink: 0,
                bgcolor: "action.selected",
                cursor: "default",
                "&:hover": {
                  bgcolor: "action.focus",
                },
              }}
            >
              <Typography
                variant="caption"
                sx={{
                  fontSize: "12px",
                  fontWeight: 600,
                  color: theme.palette.text.secondary,
                  lineHeight: 1,
                }}
              >
                +{overflow.length} More
              </Typography>
            </Box>
          </Tooltip>
        )}
      </Box>
    </Box>
  );
}
