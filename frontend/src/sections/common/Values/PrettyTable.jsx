import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { formatPath } from "./faPath";
import { MONO } from "./CodeBlock";
import { BIG_LIST, cutListText } from "./viewModes";

const parseMaybe = (v) => {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
};

const isContainer = (v) => v !== null && typeof v === "object";
const summary = (v) =>
  Array.isArray(v)
    ? `[${v.length.toLocaleString("en-US")} ${v.length === 1 ? "item" : "items"}]`
    : `{${Object.keys(v).length} ${Object.keys(v).length === 1 ? "key" : "keys"}}`;

const scalarText = (v) =>
  v === null ? "null" : typeof v === "string" ? v : String(v);

const CutRow = ({ total, depth }) => (
  <Box
    component="tr"
    data-testid="pretty-list-cut"
    sx={{
      "& td": { py: 0.5, borderBottom: "1px solid", borderColor: "divider" },
    }}
  >
    <Box
      component="td"
      colSpan={2}
      sx={{
        pl: depth * 2 + 0.5,
        typography: "s3",
        color: "text.secondary",
        fontStyle: "italic",
      }}
    >
      {cutListText(total)}
    </Box>
  </Box>
);

CutRow.propTypes = { total: PropTypes.number, depth: PropTypes.number };

const Row = ({ label, value, steps, depth }) => {
  const [open, setOpen] = useState(false);
  const v = parseMaybe(value);
  const container = isContainer(v);
  const entries = container
    ? Array.isArray(v)
      ? v.slice(0, BIG_LIST).map((x, i) => [{ type: "index", index: i }, x])
      : Object.entries(v).map(([k, x]) => [{ type: "key", key: k }, x])
    : [];
  return (
    <>
      <Box
        component="tr"
        onClick={container ? () => setOpen((o) => !o) : undefined}
        sx={{
          cursor: container ? "pointer" : "default",
          "& td": {
            borderBottom: "1px solid",
            borderColor: "divider",
            py: 0.5,
            verticalAlign: "top",
          },
          "&:hover td": container ? { bgcolor: "action.hover" } : {},
        }}
      >
        <Box
          component="td"
          sx={{
            pl: depth * 2 + 0.5,
            pr: 1.5,
            width: "34%",
            fontFamily: MONO,
            typography: "s2",
            color: "text.secondary",
            wordBreak: "break-word",
          }}
        >
          <Box
            component="span"
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}
          >
            {container && (
              <Iconify
                icon={open ? "eva:chevron-down-fill" : "eva:chevron-right-fill"}
                width={14}
              />
            )}
            {label}
          </Box>
        </Box>
        <Box
          component="td"
          sx={{
            typography: "s1",
            color: "text.primary",
            wordBreak: "break-word",
          }}
        >
          {container ? (
            <Typography
              component="span"
              typography="s2"
              color="text.disabled"
              sx={{ fontFamily: MONO }}
            >
              {summary(v)}
            </Typography>
          ) : (
            <Box
              component="span"
              sx={{
                whiteSpace: "pre-wrap",
                color: v === null ? "text.disabled" : "text.primary",
                fontVariantNumeric:
                  typeof v === "number" ? "tabular-nums" : undefined,
              }}
            >
              {scalarText(v)}
            </Box>
          )}
        </Box>
      </Box>
      {open &&
        entries.map(([s, x]) => (
          <Row
            key={JSON.stringify(s)}
            label={formatPath(null, [s])}
            value={x}
            steps={[...steps, s]}
            depth={depth + 1}
          />
        ))}
      {open && Array.isArray(v) && v.length > BIG_LIST ? (
        <CutRow total={v.length} depth={depth + 1} />
      ) : null}
    </>
  );
};

Row.propTypes = {
  label: PropTypes.string,
  value: PropTypes.any,
  steps: PropTypes.array,
  depth: PropTypes.number,
};

/**
 * PrettyTable — the `Pretty` view of an object: a Path / Value table with
 * nested objects and lists collapsed (click to open). A single-key object
 * whose value is text renders that text as markdown. integration review (RV-02):
 * a list (a wrapped conversation's messages) is the same table by index,
 * never a blank view.
 */
const PrettyTable = ({ value }) => {
  const v = parseMaybe(value);
  if (!isContainer(v)) return null;
  const list = Array.isArray(v);
  const steps = list
    ? v.slice(0, BIG_LIST).map((_, i) => ({ type: "index", index: i }))
    : Object.keys(v).map((k) => ({ type: "key", key: k }));
  const at = (step) => (step.type === "index" ? v[step.index] : v[step.key]);
  const keys = list ? [] : Object.keys(v);
  if (keys.length === 1 && typeof v[keys[0]] === "string")
    return (
      <Box>
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ fontFamily: MONO, mb: 0.5 }}
        >
          {keys[0]}
        </Typography>
        <CellMarkdown text={v[keys[0]]} fontSize={14} />
      </Box>
    );
  return (
    <Box component="table" sx={{ width: "100%", borderCollapse: "collapse" }}>
      <Box component="thead">
        <Box
          component="tr"
          sx={{
            "& th": {
              textAlign: "left",
              typography: "s3",
              fontWeight: 500,
              color: "text.secondary",
              pb: 0.5,
              borderBottom: "1px solid",
              borderColor: "divider",
            },
          }}
        >
          <Box component="th" sx={{ pl: 0.5 }}>
            Path
          </Box>
          <Box component="th">Value</Box>
        </Box>
      </Box>
      <Box component="tbody">
        {steps.map((step) => (
          <Row
            key={step.type === "index" ? `#${step.index}` : step.key}
            label={formatPath(null, [step])}
            value={at(step)}
            steps={[step]}
            depth={0}
          />
        ))}
        {list && v.length > BIG_LIST ? (
          <CutRow total={v.length} depth={0} />
        ) : null}
      </Box>
    </Box>
  );
};

PrettyTable.propTypes = { value: PropTypes.any };

export default PrettyTable;
