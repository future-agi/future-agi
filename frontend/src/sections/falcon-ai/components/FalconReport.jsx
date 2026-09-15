import React, { useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { alpha, useTheme } from "@mui/material/styles";
import { parseReport, TAG_CELL } from "../helpers/falconReport";

const html = (s) => ({ dangerouslySetInnerHTML: { __html: s } });

function Table({ block }) {
  const indexed = !block.head[0];
  return (
    <Box component="table" sx={{ width: "100%", borderCollapse: "collapse", my: 1.5 }}>
      <Box component="thead">
        <Box component="tr">
          {block.head.map((h, i) => (
            <Box
              component="th"
              key={i}
              sx={{
                textAlign: "left",
                px: 1,
                py: 0.75,
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: "text.secondary",
                borderBottom: "1.5px solid",
                borderColor: "text.primary",
              }}
              {...html(h)}
            />
          ))}
        </Box>
      </Box>
      <Box component="tbody">
        {block.rows.map((r, ri) => (
          <Box component="tr" key={ri}>
            {r.map((c, ci) => (
              <Box
                component="td"
                key={ci}
                sx={{
                  px: 1,
                  py: 0.9,
                  fontSize: 13,
                  verticalAlign: "top",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  ...(indexed && !ci
                    ? { width: 24, color: "primary.main", fontWeight: 700 }
                    : null),
                }}
                {...html(TAG_CELL(c))}
              />
            ))}
          </Box>
        ))}
      </Box>
    </Box>
  );
}

Table.propTypes = { block: PropTypes.object.isRequired };

function Block({ block }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const b = block;

  switch (b.type) {
    case "h1":
      return (
        <Box
          component="h1"
          sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em", m: 0, mb: 0.5 }}
          {...html(b.html)}
        />
      );
    case "subtitle":
      return (
        <Box sx={{ fontSize: 14, color: "text.secondary", mb: 2 }} {...html(b.html)} />
      );
    case "lede":
      return <Box sx={{ fontSize: 14, lineHeight: 1.7, mb: 1.5 }} {...html(b.html)} />;
    case "step":
      return (
        <Box
          component="h2"
          sx={{ fontSize: 15, fontWeight: 700, mt: 2.5, mb: 0.75, display: "flex", gap: 1 }}
        >
          <Box component="span" sx={{ color: "primary.main" }}>
            {b.step}
          </Box>
          <Box component="span" {...html(b.html)} />
        </Box>
      );
    case "h2":
      return (
        <Box
          component="h2"
          sx={{ fontSize: 15, fontWeight: 700, mt: 2.5, mb: 0.75 }}
          {...html(b.html)}
        />
      );
    case "h3":
      return (
        <Box
          component="h3"
          sx={{ fontSize: 13.5, fontWeight: 700, mt: 1.5, mb: 0.5 }}
          {...html(b.html)}
        />
      );
    case "muted":
      return (
        <Box sx={{ fontSize: 12, color: "text.secondary", mt: 1 }} {...html(b.html)} />
      );
    case "note":
      return (
        <Box
          sx={{
            borderLeft: 3,
            borderColor: "primary.main",
            bgcolor: alpha(theme.palette.primary.main, isDark ? 0.12 : 0.07),
            borderRadius: "0 6px 6px 0",
            px: 1.5,
            py: 1.25,
            my: 1.5,
            fontSize: 13,
          }}
          {...html(b.html)}
        />
      );
    case "solves":
      return (
        <Box
          sx={{ borderLeft: 3, borderColor: "primary.main", pl: 1.75, my: 1.5, fontSize: 13.5 }}
        >
          {b.solves.map(([lead, rest], i) => (
            <Box key={i} sx={{ mb: 0.75 }}>
              <Box component="span" sx={{ fontWeight: 700, color: "primary.main" }} {...html(lead)} />{" "}
              <Box component="span" {...html(rest)} />
            </Box>
          ))}
        </Box>
      );
    case "stats":
      return (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2, my: 2 }}>
          {b.stats.map((s, i) => (
            <Box key={i} sx={{ flex: "1 1 120px", borderLeft: 3, borderColor: "primary.main", pl: 1.5 }}>
              <Box sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.03em", lineHeight: 1.05 }}>
                {s.n}
              </Box>
              <Box sx={{ fontSize: 10.5, color: "text.secondary", mt: 0.5, letterSpacing: "0.04em" }}>
                {s.label}
              </Box>
            </Box>
          ))}
        </Box>
      );
    case "prompt":
    case "code":
      return (
        <Box
          component="pre"
          sx={{
            m: "8px 0",
            p: 1.5,
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            fontFamily: "'SF Mono', Menlo, Consolas, monospace",
            fontSize: b.type === "prompt" ? 11 : 12,
            lineHeight: 1.5,
            borderRadius: "6px",
            border: b.type === "prompt" ? 1 : 0,
            borderColor: "divider",
            bgcolor: isDark
              ? alpha(theme.palette.common.white, 0.05)
              : alpha(theme.palette.common.black, 0.035),
          }}
        >
          {b.value}
        </Box>
      );
    case "list": {
      const tag = b.ordered ? "ol" : "ul";
      return (
        <Box component={tag} sx={{ pl: 2.5, my: 1, fontSize: 14, lineHeight: 1.7 }}>
          {b.items.map((it, i) => (
            <Box component="li" key={i} sx={{ mb: 0.5 }} {...html(it)} />
          ))}
        </Box>
      );
    }
    case "table":
      return <Table block={b} />;
    default:
      return <Box sx={{ fontSize: 14, lineHeight: 1.7, mb: 1.25 }} {...html(b.html)} />;
  }
}

Block.propTypes = { block: PropTypes.object.isRequired };

export default function FalconReport({ content }) {
  const doc = useMemo(() => parseReport(content), [content]);
  if (!doc.pages.length) return null;

  return (
    <Box
      sx={{
        color: "text.primary",
        wordBreak: "break-word",
        "& code": {
          fontFamily: "'SF Mono', Menlo, Consolas, monospace",
          fontSize: "0.86em",
          bgcolor: "action.hover",
          borderRadius: "3px",
          px: 0.6,
          py: 0.2,
        },
        "& a": { color: "primary.main", textDecoration: "none" },
        "& .tag": {
          display: "inline-block",
          ml: 0.75,
          px: 0.75,
          py: "1px",
          borderRadius: "9px",
          fontSize: 9,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          verticalAlign: "1px",
          bgcolor: "action.selected",
          color: "text.secondary",
        },
        "& .tag.t-b": { bgcolor: "#ECE8FF", color: "#5A41BD" },
      }}
    >
      {doc.pages.map((p, pi) =>
        p.blocks.map((b, bi) => <Block key={`${pi}-${bi}`} block={b} />),
      )}
    </Box>
  );
}

FalconReport.propTypes = { content: PropTypes.string };
