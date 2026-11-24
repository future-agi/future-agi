import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

const MONO_FONT = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";

export function HighlightedProse({
  text,
  terms = [],
  role = "danger",
  onClaimClick = null,
}) {
  const nodes = useMemo(() => {
    const src = String(text || "");
    const cleanTerms = terms.filter((t) => t && t.length >= 2);
    const hlBg = role === "warning" ? "status.warning.bg" : "status.danger.bg";
    const hlFg = role === "warning" ? "status.warning.fg" : "status.danger.fg";

    const renderHighlights = (str, keyPrefix) => {
      if (!cleanTerms.length) return str;
      const escaped = cleanTerms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
      const rx = new RegExp(`(${escaped.join("|")})`, "gi");
      return str.split(rx).map((part, idx) => {
        const hit = cleanTerms.some((t) => t.toLowerCase() === part.toLowerCase());
        if (!hit) return <React.Fragment key={`${keyPrefix}-${idx}`}>{part}</React.Fragment>;
        const interactive = Boolean(onClaimClick);
        return (
          <Box
            key={`${keyPrefix}-${idx}`}
            component={interactive ? "button" : "mark"}
            type={interactive ? "button" : undefined}
            onClick={interactive ? (e) => onClaimClick(e.currentTarget, part) : undefined}
            sx={{
              display: "inline",
              border: "none",
              font: "inherit",
              bgcolor: hlBg,
              color: hlFg,
              px: 0.5,
              py: 0.1,
              borderRadius: "4px",
              fontWeight: 500,
              ...(interactive
                ? {
                    cursor: "pointer",
                    textDecoration: "underline",
                    textDecorationStyle: "dotted",
                    textUnderlineOffset: "3px",
                  }
                : {}),
            }}
          >
            {part}
          </Box>
        );
      });
    };

    const tokenRx = /(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|`[^`]+`)/g;
    return src.split(tokenRx).map((seg, i) => {
      const linkMatch = seg.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        const [, label, href] = linkMatch;
        const isHttp = /^https?:\/\//i.test(href);
        return (
          <Box
            key={i}
            component={isHttp ? "a" : "span"}
            href={isHttp ? href : undefined}
            target={isHttp ? "_blank" : undefined}
            rel={isHttp ? "noopener noreferrer" : undefined}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              px: 1,
              py: 0.35,
              borderRadius: "6px",
              bgcolor: "background.paper",
              color: isHttp ? "primary.main" : "text.secondary",
              typography: "s3",
              textDecoration: "none",
              verticalAlign: "baseline",
            }}
          >
            <Iconify icon="solar:document-text-linear" width={12} />
            {label}
            {!isHttp ? " · recorded artifact" : ""}
          </Box>
        );
      }
      if (seg.startsWith("**") && seg.endsWith("**") && seg.length > 4) {
        return (
          <Box key={i} component="strong" sx={{ fontWeight: 600, color: "text.primary" }}>
            {renderHighlights(seg.slice(2, -2), `b${i}`)}
          </Box>
        );
      }
      if (seg.startsWith("`") && seg.endsWith("`") && seg.length > 2) {
        return (
          <Box
            key={i}
            component="code"
            sx={{
              fontFamily: MONO_FONT,
              px: 0.5,
              py: "1px",
              borderRadius: "4px",
              bgcolor: "action.selected",
              color: "text.primary",
            }}
          >
            {seg.slice(1, -1)}
          </Box>
        );
      }
      return <React.Fragment key={i}>{renderHighlights(seg, `t${i}`)}</React.Fragment>;
    });
  }, [text, terms, role, onClaimClick]);

  return <>{nodes}</>;
}

HighlightedProse.propTypes = {
  text: PropTypes.string,
  terms: PropTypes.arrayOf(PropTypes.string),
  role: PropTypes.string,
  onClaimClick: PropTypes.func,
};

function parseTextWithTables(raw) {
  const lines = String(raw || "").split("\n");
  const blocks = [];
  let textBuf = [];

  const flushText = () => {
    const joined = textBuf.join("\n").trim();
    if (joined) blocks.push({ type: "text", text: joined });
    textBuf = [];
  };

  const splitRow = (line) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());

  const isSep = (line) => {
    const t = line.trim();
    return t.includes("|") && /^\|?[\s:|-]+\|?$/.test(t) && t.includes("-");
  };

  let i = 0;
  while (i < lines.length) {
    const cur = lines[i];
    const next = lines[i + 1];
    if (cur && cur.includes("|") && next && isSep(next)) {
      flushText();
      const rawHeader = splitRow(cur);
      const dropIdxCol = rawHeader.length > 2 && rawHeader[0] === "";
      const header = dropIdxCol ? rawHeader.slice(1) : rawHeader;
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim().length > 2) {
        const cells = splitRow(lines[i]);
        rows.push(dropIdxCol ? cells.slice(1) : cells);
        i += 1;
      }
      blocks.push({ type: "table", header, rows });
      continue;
    }
    textBuf.push(cur);
    i += 1;
  }
  flushText();
  return blocks;
}

const BOILERPLATE_COL_RE = /^(vendor|location|store|company|branch|account|restaurant|site)/i;

function SmartEvidenceTable({ header, rows, maxRows = 5, truncation = null }) {
  const [showAllCols, setShowAllCols] = useState(false);
  const visibleRows = rows.slice(0, maxRows);
  const softLine = "divider";

  const { displayedCols, hiddenConstantCount } = useMemo(() => {
    const base = header.map((h, ci) => ({ h, ci }));
    if (showAllCols || header.length <= 5 || visibleRows.length < 2) {
      return { displayedCols: base, hiddenConstantCount: 0 };
    }
    const kept = [];
    let hidden = 0;
    base.forEach((col, pos) => {
      const vals = new Set(visibleRows.map((r) => String(r[col.ci] ?? "").trim()));
      const isBoilerplate = pos > 0 && vals.size <= 1 && (BOILERPLATE_COL_RE.test(col.h) || pos >= 5);
      if (isBoilerplate) hidden += 1;
      else kept.push(col);
    });
    return {
      displayedCols: kept.length >= 2 ? kept : base,
      hiddenConstantCount: kept.length >= 2 ? hidden : 0,
    };
  }, [header, visibleRows, showAllCols]);

  return (
    <Stack gap={0.65}>
      <Box
        sx={{
          overflowX: "auto",
          borderRadius: "8px",
          bgcolor: "background.paper",
        }}
      >
        <Table
          size="small"
          sx={{
            minWidth: 280,
            "& .MuiTableCell-root": { borderColor: softLine },
            "& tbody tr:last-of-type .MuiTableCell-root": { borderBottom: 0 },
          }}
        >
          <TableHead>
            <TableRow>
              {displayedCols.map(({ h, ci }) => (
                <TableCell
                  key={ci}
                  sx={{ whiteSpace: "nowrap", py: 0.65, px: 1.15, bgcolor: "transparent" }}
                >
                  <Typography typography="s3" fontWeight={600} color="text.secondary">
                    {h.replace(/_/g, " ")}
                  </Typography>
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {visibleRows.map((row, ri) => (
              <TableRow key={ri}>
                {displayedCols.map(({ ci }) => {
                  const val = row[ci] ?? "—";
                  return (
                    <TableCell
                      key={ci}
                      sx={{
                        whiteSpace: "nowrap",
                        py: 0.5,
                        px: 1,
                        maxWidth: 185,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      <Tooltip title={String(val).length > 22 ? String(val) : ""} arrow>
                        <Typography typography="s3" color="text.primary" noWrap>
                          {val}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>

      <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1} flexWrap="wrap">
        <Typography typography="s3" color="text.secondary">
          {truncation
            ? `${truncation.shown} of ${truncation.total} rows shown in preview${truncation.has_csv_pointer ? " · CSV link returned" : ""}`
            : `${visibleRows.length} ${visibleRows.length === 1 ? "row" : "rows"} displayed`}
        </Typography>
        <Stack direction="row" alignItems="center" gap={1}>
          {displayedCols.length >= 4 && (
            <Typography typography="s3" color="text.tertiary">
              Scroll →
            </Typography>
          )}
          {(hiddenConstantCount > 0 || showAllCols) && (
            <Button
              size="small"
              variant="text"
              onClick={() => setShowAllCols((v) => !v)}
              sx={{ color: "text.secondary", minWidth: 0, py: 0 }}
            >
              {showAllCols
                ? "Hide repeated"
                : `+${hiddenConstantCount} repeated ${hiddenConstantCount === 1 ? "col" : "cols"}`}
            </Button>
          )}
        </Stack>
      </Stack>
    </Stack>
  );
}

SmartEvidenceTable.propTypes = {
  header: PropTypes.array.isRequired,
  rows: PropTypes.array.isRequired,
  maxRows: PropTypes.number,
  truncation: PropTypes.object,
};

export default function EvidenceRichBody({
  text,
  terms = [],
  role = "danger",
  maxRows = 5,
  truncation = null,
  onClaimClick = null,
}) {
  const blocks = useMemo(() => parseTextWithTables(text), [text]);
  if (!blocks.length) return null;

  return (
    <Stack gap={0.9}>
      {blocks.map((blk, idx) =>
        blk.type === "text" ? (
          <Typography
            key={idx}
            typography="s3"
            color="text.primary"
            sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.55 }}
          >
            <HighlightedProse
              text={blk.text}
              terms={terms}
              role={role}
              onClaimClick={onClaimClick}
            />
          </Typography>
        ) : (
          <SmartEvidenceTable
            key={idx}
            header={blk.header}
            rows={blk.rows}
            maxRows={maxRows}
            truncation={truncation}
          />
        ),
      )}
    </Stack>
  );
}

EvidenceRichBody.propTypes = {
  text: PropTypes.string,
  terms: PropTypes.arrayOf(PropTypes.string),
  role: PropTypes.string,
  maxRows: PropTypes.number,
  truncation: PropTypes.object,
  onClaimClick: PropTypes.func,
};
