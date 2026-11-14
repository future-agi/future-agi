/* eslint-disable react/prop-types */
import { Box, Typography, ButtonBase } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";

/**
 * Renders a JSON value as an expandable tree.
 * For objects/arrays: shows expand/collapse with nested key-value rows.
 * For primitives: shows the value inline.
 */
export function JsonValueTree({ value, expanded, onToggle }) {
  let parsed;
  try {
    parsed = typeof value === "string" ? JSON.parse(value) : value;
  } catch {
    return (
      <Typography
        variant="caption"
        component="pre"
        sx={{
          fontFamily: "monospace",
          fontSize: "12px",
          color: "primary.main",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          m: 0,
        }}
      >
        {value}
      </Typography>
    );
  }

  if (parsed === null || parsed === undefined) {
    return (
      <Typography variant="caption" color="text.disabled">
        null
      </Typography>
    );
  }

  if (typeof parsed !== "object") {
    return (
      <Typography
        variant="caption"
        color="primary.main"
        sx={{ fontSize: "12px" }}
      >
        {String(parsed)}
      </Typography>
    );
  }

  return (
    <Box>
      <ButtonBase
        onClick={onToggle}
        sx={{
          display: "flex",
          justifyContent: "flex-start",
          alignItems: "center",
          gap: 0.5,
          cursor: "pointer",
          "&:hover": { opacity: 0.7 },
        }}
      >
        <Iconify
          icon={
            expanded
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={14}
          sx={{ color: "text.disabled" }}
        />
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "12px" }}
        >
          {Array.isArray(parsed)
            ? `Array (${parsed.length} items)`
            : `Object (${Object.keys(parsed).length} keys)`}
        </Typography>
      </ButtonBase>
      {expanded && (
        <Box
          sx={{
            ml: 1.5,
            mt: 0.5,
            borderLeft: "1px solid",
            borderColor: "divider",
            pl: 1,
          }}
        >
          <JsonEntries data={parsed} />
        </Box>
      )}
    </Box>
  );
}

export function JsonEntries({ data, depth = 0 }) {
  if (depth > 5) {
    return (
      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ fontSize: "12px" }}
      >
        ...
      </Typography>
    );
  }
  const entries = Array.isArray(data)
    ? data.map((v, i) => [String(i), v])
    : Object.entries(data);
  return (
    <Box sx={{ display: "flex", flexDirection: "column" }}>
      {entries.map(([key, val]) => (
        <JsonEntryRow
          key={key}
          entryKey={key}
          entryValue={val}
          isObject={val !== null && typeof val === "object"}
          depth={depth}
        />
      ))}
    </Box>
  );
}

function JsonEntryRow({ entryKey, entryValue, isObject, depth }) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ py: 0.25 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 0.5,
          cursor: isObject ? "pointer" : "default",
          "&:hover": isObject
            ? { backgroundColor: "action.hover", borderRadius: "4px" }
            : {},
          px: 0.5,
          py: 0.15,
        }}
        onClick={() => isObject && setOpen(!open)}
      >
        {isObject ? (
          <Iconify
            icon={
              open
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
            width={12}
            sx={{ color: "text.disabled", mt: 0.25, flexShrink: 0 }}
          />
        ) : (
          <Box sx={{ width: 12, flexShrink: 0 }} />
        )}
        <Typography
          variant="caption"
          fontWeight={600}
          sx={{
            fontSize: "12px",
            minWidth: 60,
            flexShrink: 0,
            color: "text.secondary",
          }}
        >
          {entryKey}
        </Typography>
        {!isObject && (
          <Typography
            variant="caption"
            sx={{
              fontSize: "12px",
              color: "primary.main",
              wordBreak: "break-all",
              overflow: "hidden",
              textOverflow: "ellipsis",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {entryValue === null
              ? "null"
              : entryValue === true
                ? "true"
                : entryValue === false
                  ? "false"
                  : String(entryValue)}
          </Typography>
        )}
        {isObject && !open && (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ fontSize: "12px" }}
          >
            {Array.isArray(entryValue)
              ? `[${entryValue.length}]`
              : `{${Object.keys(entryValue).length}}`}
          </Typography>
        )}
      </Box>
      {isObject && open && (
        <Box
          sx={{
            ml: 2,
            borderLeft: "1px solid",
            borderColor: "divider",
            pl: 0.75,
          }}
        >
          <JsonEntries data={entryValue} depth={depth + 1} />
        </Box>
      )}
    </Box>
  );
}

JsonValueTree.propTypes = {
  value: PropTypes.any,
  expanded: PropTypes.bool,
  onToggle: PropTypes.func,
};
