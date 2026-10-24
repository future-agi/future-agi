import React, { useId, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Paper, Popper, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";

// A grid of cells drawn as a handful of SVG paths, one per fill, so a month
// of hours (744 cells) costs a few dozen nodes rather than one element per
// cell. One hover layer finds the cell under the pointer, and the keyboard
// moves a cursor (arrow keys, Enter to select) whose cell is read out.

const roundRect = (x, y, w, h, r) => {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  if (!rr) return `M${x},${y}h${w}v${h}h${-w}Z`;
  return `M${x + rr},${y}h${w - 2 * rr}a${rr},${rr} 0 0 1 ${rr},${rr}v${h - 2 * rr}a${rr},${rr} 0 0 1 ${-rr},${rr}h${-(w - 2 * rr)}a${rr},${rr} 0 0 1 ${-rr},${-rr}v${-(h - 2 * rr)}a${rr},${rr} 0 0 1 ${rr},${-rr}Z`;
};

/**
 * `paint(r, c)` gives a cell's look: null for no cell, else {fill, hatch?,
 * outline?: "solid" | "dashed", dot?, text?, ink?}. `describe(r, c)` is its
 * words (tooltip and keyboard read-out), `onPick(r, c)` selects it. `rowLabel`
 * and `colLabel` return the axis text (empty for none), `rightText(r)` a
 * column of figures after the cells, and `footer` an extra row of cells
 * ({label, paint(c), describe(c), rightText}) below a gap.
 */
export default function CellGrid({
  rowCount,
  colCount,
  cellW,
  cellH,
  gap = 2,
  radius = 2,
  labelW = 0,
  rightW = 0,
  topH = 0,
  bottomH = 0,
  width,
  paint,
  describe,
  onPick,
  rowLabel,
  colLabel,
  colLabelAt = "bottom",
  rightText,
  footer,
  ariaLabel,
  labelSize = 11,
}) {
  const theme = useTheme();
  const svg = useRef(null);
  const id = useId().replace(/:/g, "");
  const [hover, setHover] = useState(null); // {r, c}
  const [cursor, setCursor] = useState(null); // {r, c}
  const pitchX = cellW + gap;
  const pitchY = cellH + gap;
  const footerGap = footer ? 6 : 0;
  const rowsH = rowCount * pitchY - gap;
  const footerY = topH + rowsH + footerGap + gap;
  const gridBottom = footer ? footerY + cellH : topH + rowsH;
  const height = gridBottom + bottomH;
  const svgWidth =
    width ?? labelW + colCount * pitchX - gap + (rightW ? rightW : 0);
  const xOf = (c) => labelW + c * pitchX;
  const yOf = (r) => (r === rowCount ? footerY : topH + r * pitchY);
  const cellAt = (r, c) => (r === rowCount ? footer?.paint(c) : paint(r, c));

  // Every cell grouped by how it is drawn.
  const layers = useMemo(() => {
    const fills = new Map();
    const hatches = new Map();
    const outlines = { solid: [], dashed: [] };
    const dots = [];
    const texts = [];
    const rows = rowCount + (footer ? 1 : 0);
    for (let r = 0; r < rows; r += 1)
      for (let c = 0; c < colCount; c += 1) {
        const cell = cellAt(r, c);
        if (!cell) continue;
        const x = xOf(c);
        const y = yOf(r);
        const d = roundRect(x, y, cellW, cellH, radius);
        if (cell.fill) {
          if (!fills.has(cell.fill)) fills.set(cell.fill, []);
          fills.get(cell.fill).push(d);
        }
        if (cell.hatch) {
          if (!hatches.has(cell.hatch)) hatches.set(cell.hatch, []);
          hatches.get(cell.hatch).push(d);
        }
        if (cell.outline)
          outlines[cell.outline].push(
            roundRect(x + 0.5, y + 0.5, cellW - 1, cellH - 1, radius),
          );
        if (cell.dot)
          dots.push(
            `M${x + cellW - 3.5},${y + 3.5}m-2.5,0a2.5,2.5 0 1 0 5,0a2.5,2.5 0 1 0 -5,0`,
          );
        if (cell.text)
          texts.push({
            key: `${r}|${c}`,
            x,
            y,
            text: cell.text,
            ink: cell.ink,
          });
      }
    return {
      fills: [...fills.entries()].map(([fill, list]) => [fill, list.join("")]),
      hatches: [...hatches.entries()].map(([color, list], i) => [
        color,
        list.join(""),
        `${id}-h${i}`,
      ]),
      outlines: {
        solid: outlines.solid.join(""),
        dashed: outlines.dashed.join(""),
      },
      dots: dots.join(""),
      texts,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paint, footer, rowCount, colCount, cellW, cellH, gap, labelW, topH]);

  const locate = (event) => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect) return null;
    const x = event.clientX - rect.left - labelW;
    const y = event.clientY - rect.top;
    const c = Math.floor(x / pitchX);
    if (c < 0 || c >= colCount || x - c * pitchX > cellW) return null;
    if (footer && y >= footerY && y <= footerY + cellH)
      return { r: rowCount, c };
    const r = Math.floor((y - topH) / pitchY);
    if (r < 0 || r >= rowCount || y - topH - r * pitchY > cellH) return null;
    return { r, c };
  };
  const words = (at) =>
    at
      ? at.r === rowCount
        ? footer?.describe(at.c) || ""
        : describe(at.r, at.c)
      : "";
  const pick = (at) => {
    if (!at || at.r === rowCount || !paint(at.r, at.c)) return;
    onPick?.(at.r, at.c);
  };
  const shown = hover || cursor;
  const anchor = useMemo(() => {
    if (!shown || !svg.current) return null;
    const rect = svg.current.getBoundingClientRect();
    const left = rect.left + xOf(shown.c);
    const top = rect.top + yOf(shown.r);
    return {
      getBoundingClientRect: () => ({
        left,
        top,
        right: left + cellW,
        bottom: top + cellH,
        width: cellW,
        height: cellH,
        x: left,
        y: top,
        toJSON: () => null,
      }),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, cellW, cellH]);

  const font = theme.typography.fontFamily;
  return (
    <Box sx={{ position: "relative" }}>
      <Box
        component="svg"
        ref={svg}
        width={svgWidth}
        height={height}
        role="img"
        tabIndex={0}
        aria-label={`${ariaLabel}. Use the arrow keys to move between cells and Enter to select one.`}
        onMouseMove={(event) => setHover(locate(event))}
        onMouseLeave={() => setHover(null)}
        onClick={(event) => pick(locate(event))}
        onKeyDown={(event) => {
          const moves = {
            ArrowRight: [0, 1],
            ArrowLeft: [0, -1],
            ArrowDown: [1, 0],
            ArrowUp: [-1, 0],
          };
          if (moves[event.key]) {
            event.preventDefault();
            const [dr, dc] = moves[event.key];
            setCursor((at) =>
              at
                ? {
                    r: Math.min(
                      rowCount - (footer ? 0 : 1),
                      Math.max(0, at.r + dr),
                    ),
                    c: Math.min(colCount - 1, Math.max(0, at.c + dc)),
                  }
                : { r: 0, c: 0 },
            );
          } else if ((event.key === "Enter" || event.key === " ") && cursor) {
            event.preventDefault();
            pick(cursor);
          } else if (event.key === "Escape") setCursor(null);
        }}
        onBlur={() => setCursor(null)}
        sx={{
          display: "block",
          cursor: "pointer",
          outline: "none",
          "&:focus-visible": {
            outline: `2px solid ${theme.palette.primary.main}`,
            outlineOffset: 2,
            borderRadius: "4px",
          },
        }}
      >
        <defs>
          {layers.hatches.map(([color, , patternId]) => (
            <pattern
              key={patternId}
              id={patternId}
              width={4}
              height={4}
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(135)"
            >
              <line
                x1={0}
                y1={0}
                x2={0}
                y2={4}
                stroke={alpha(color, 0.6)}
                strokeWidth={1.5}
              />
            </pattern>
          ))}
        </defs>
        {rowLabel &&
          Array.from({ length: rowCount }, (_, r) => {
            const text = rowLabel(r);
            return text ? (
              <text
                key={`row-${r}`}
                x={0}
                y={yOf(r) + cellH / 2 + labelSize * 0.35}
                fontSize={labelSize}
                fill={theme.palette.text.secondary}
                fontFamily={font}
              >
                {text}
              </text>
            ) : null;
          })}
        {footer?.label && (
          <text
            x={0}
            y={footerY + cellH / 2 + labelSize * 0.35}
            fontSize={labelSize}
            fill={theme.palette.text.disabled}
            fontFamily={font}
          >
            {footer.label}
          </text>
        )}
        {colLabel &&
          Array.from({ length: colCount }, (_, c) => {
            const text = colLabel(c);
            return text ? (
              <text
                key={`col-${c}`}
                x={xOf(c)}
                y={colLabelAt === "top" ? topH - 5 : gridBottom + 12}
                fontSize={10}
                fill={theme.palette.text.disabled}
                fontFamily={font}
              >
                {text}
              </text>
            ) : null;
          })}
        {layers.fills.map(([fill, d]) => (
          <path key={fill} d={d} fill={fill} />
        ))}
        {layers.hatches.map(([, d, patternId]) => (
          <path key={patternId} d={d} fill={`url(#${patternId})`} />
        ))}
        {layers.outlines.solid && (
          <path
            d={layers.outlines.solid}
            fill="none"
            stroke={alpha(theme.palette.text.disabled, 0.6)}
            strokeWidth={1}
          />
        )}
        {layers.outlines.dashed && (
          <path
            d={layers.outlines.dashed}
            fill="none"
            stroke={alpha(theme.palette.text.disabled, 0.6)}
            strokeWidth={1}
            strokeDasharray="2 2"
          />
        )}
        {layers.texts.map((t) => (
          <text
            key={t.key}
            x={t.x + cellW / 2}
            y={t.y + cellH / 2 + 3.5}
            textAnchor="middle"
            fontSize={10}
            fontWeight={600}
            fill={t.ink || theme.palette.text.primary}
            fontFamily={font}
            style={{ fontVariantNumeric: "tabular-nums" }}
            pointerEvents="none"
          >
            {t.text}
          </text>
        ))}
        {layers.dots && (
          <path
            d={layers.dots}
            fill={theme.palette.error.main}
            stroke={theme.palette.background.paper}
            strokeWidth={1}
            data-errors
          />
        )}
        {rightText &&
          [
            ...Array.from({ length: rowCount }, (_, r) => [r, rightText(r)]),
            ...(footer?.rightText ? [[rowCount, footer.rightText]] : []),
          ].map(([r, text]) =>
            text ? (
              <text
                key={`right-${r}`}
                x={svgWidth}
                y={yOf(r) + cellH / 2 + 3.5}
                textAnchor="end"
                fontSize={11}
                fill={theme.palette.text.secondary}
                fontFamily={font}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {text}
              </text>
            ) : null,
          )}
        {shown && (
          <rect
            x={xOf(shown.c) - 1}
            y={yOf(shown.r) - 1}
            width={cellW + 2}
            height={cellH + 2}
            rx={radius + 1}
            fill="none"
            stroke={
              cursor && !hover
                ? theme.palette.primary.main
                : theme.palette.text.primary
            }
            strokeWidth={1.5}
            pointerEvents="none"
          />
        )}
      </Box>
      <Box
        aria-live="polite"
        role="status"
        sx={{
          position: "absolute",
          width: 1,
          height: 1,
          overflow: "hidden",
          clip: "rect(0 0 0 0)",
        }}
      >
        {cursor ? words(cursor) : ""}
      </Box>
      <Popper
        open={Boolean(anchor)}
        anchorEl={anchor}
        placement="top"
        sx={{ zIndex: theme.zIndex.modal + 1, pointerEvents: "none" }}
        modifiers={[{ name: "offset", options: { offset: [0, 6] } }]}
      >
        {shown && (
          <Paper
            elevation={0}
            sx={{
              maxWidth: 320,
              px: 1.25,
              py: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              boxShadow: (t) => t.customShadows?.dropdown,
            }}
          >
            <Typography variant="body2" sx={{ fontSize: 12.5 }}>
              {words(shown)}
            </Typography>
          </Paper>
        )}
      </Popper>
    </Box>
  );
}

CellGrid.propTypes = {
  rowCount: PropTypes.number.isRequired,
  colCount: PropTypes.number.isRequired,
  cellW: PropTypes.number.isRequired,
  cellH: PropTypes.number.isRequired,
  gap: PropTypes.number,
  radius: PropTypes.number,
  labelW: PropTypes.number,
  rightW: PropTypes.number,
  topH: PropTypes.number,
  bottomH: PropTypes.number,
  width: PropTypes.number,
  paint: PropTypes.func.isRequired,
  describe: PropTypes.func.isRequired,
  onPick: PropTypes.func,
  rowLabel: PropTypes.func,
  colLabel: PropTypes.func,
  colLabelAt: PropTypes.oneOf(["top", "bottom"]),
  rightText: PropTypes.func,
  footer: PropTypes.object,
  ariaLabel: PropTypes.string.isRequired,
  labelSize: PropTypes.number,
};
