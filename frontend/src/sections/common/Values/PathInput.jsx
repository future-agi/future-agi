// FA Path input (R2-SPEC P6 §3.3; W0a minimal → L6): autocomplete from the
// json-schema's typed `paths[]` (type icon, path, coverage, example),
// `[` suggests `[0]` `[-1]` `[*]`, loud `Invalid path` errors with the
// position, and a live preview against a sample row.
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ClickAwayListener,
  Paper,
  Popper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { parsePath, resolvePath } from "./faPath";
import { kindIcon } from "./kindIcons";
import { coveragePercent, dominantKind, exampleText } from "./inferSchema";
import { MONO } from "./CodeBlock";

const PREVIEW_MAX = 80;
const MAX_OPTIONS = 60;

/** `→ "enterprise"` / `→ 129` / `→ not found in this row`. */
export function previewText(sampleRow, parsed) {
  if (!parsed?.ok || !sampleRow) return null;
  const base = parsed.column ? sampleRow[parsed.column] : sampleRow;
  if (parsed.column && base === undefined) return "→ not found in this row";
  const r = resolvePath(base, parsed.steps);
  if (!r.found) return "→ not found in this row";
  let text;
  try {
    text = JSON.stringify(r.value);
  } catch {
    text = String(r.value);
  }
  if (text === undefined) text = String(r.value);
  return `→ ${text.length > PREVIEW_MAX ? `${text.slice(0, PREVIEW_MAX - 1)}…` : text}`;
}

/** Every typed path in a json-schema response (`{colId: {paths}}`). */
export function schemaPathOptions(schemas, columns) {
  const out = [];
  Object.values(schemas ?? {}).forEach((entry) => {
    if (!entry) return;
    if (
      Array.isArray(columns) &&
      columns.length &&
      !columns.includes(entry.name)
    )
      return;
    (entry.paths ?? []).forEach((p) => out.push({ ...p, column: entry.name }));
  });
  return out;
}

/** Options for the typed text: substring match, `[` → index suggestions. */
export function filterPathOptions(options, text) {
  const t = String(text ?? "").trim();
  if (t.endsWith("[")) {
    const base = t.slice(0, -1);
    return ["[0]", "[-1]", "[*]"].map((s) => ({
      path: `${base}${s}`,
      synthetic: true,
      hint:
        s === "[0]" ? "first item" : s === "[-1]" ? "last item" : "every item",
    }));
  }
  const lower = t.toLowerCase();
  const hits = lower
    ? options.filter(
        (o) => o.path.toLowerCase().includes(lower) && o.path !== t,
      )
    : options;
  return hits
    .sort((a, b) => {
      const as = a.path.toLowerCase().startsWith(lower) ? 0 : 1;
      const bs = b.path.toLowerCase().startsWith(lower) ? 0 : 1;
      return as - bs;
    })
    .slice(0, MAX_OPTIONS);
}

const Option = ({ option, active, onPick }) => {
  const kind = option.synthetic ? "array" : dominantKind(option.kinds);
  const cov = option.coverage;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      role="option"
      aria-selected={active}
      onMouseDown={(e) => {
        e.preventDefault();
        onPick(option);
      }}
      sx={{
        px: 1.25,
        py: 0.625,
        cursor: "pointer",
        bgcolor: active
          ? (t) => alpha(t.palette.primary.main, 0.08)
          : "transparent",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Iconify
        icon={kindIcon(kind)}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography
          typography="s2"
          noWrap
          sx={{ fontFamily: MONO, color: "text.primary" }}
        >
          {option.path}
        </Typography>
        <Typography typography="s3" color="text.secondary" noWrap>
          {option.synthetic
            ? option.hint
            : [
                cov
                  ? `${cov.present.toLocaleString("en-US")} of ${cov.total.toLocaleString("en-US")} rows`
                  : null,
                option.example !== null && option.example !== undefined
                  ? `e.g. ${exampleText(option.example)}`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
        </Typography>
      </Box>
      {cov && (
        <Typography
          typography="s3"
          sx={{
            flexShrink: 0,
            color: cov.present === cov.total ? "text.secondary" : "amber.700",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {coveragePercent(cov)}
        </Typography>
      )}
    </Stack>
  );
};

Option.propTypes = {
  option: PropTypes.object.isRequired,
  active: PropTypes.bool,
  onPick: PropTypes.func,
};

/**
 * An FA Path input (faPath.js): monospace TextField that validates on
 * change (`Invalid path · Unexpected "%" at 7`), suggests typed paths from
 * `schemas` (type icon, coverage, example; ↑/↓/Enter pick, Tab accepts the
 * top one, Esc closes) and, with `sampleRow`, previews the resolved value.
 * Unknown paths are allowed (the preview says `not found`).
 *
 * Props (frozen, R2 manifest row 39):
 * @param {string} props.value
 * @param {(value: string, parsed: object) => void} props.onChange
 * @param {string[]} [props.columns]  column names (first token)
 * @param {object} [props.schemas]  json-schema per column (`paths[]`)
 * @param {object} [props.sampleRow]  `{columnName: value}` for the preview
 * @param {string} [props.placeholder="e.g. metadata.customer.tier"]
 * @param {"small"|"medium"} [props.size="small"]
 * @param {string} [props.error]  an outside error (wins over parsing)
 * @param {(value: string) => void} [props.onEnter]
 */
const PathInput = ({
  value,
  onChange,
  columns,
  schemas,
  sampleRow,
  placeholder = "e.g. metadata.customer.tier",
  size = "small",
  error,
  onEnter,
  autoFocus = false,
}) => {
  const text = value ?? "";
  const anchor = useRef(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const parsed = useMemo(
    () => (text.trim() ? parsePath(text, { columns }) : null),
    [text, columns],
  );
  const allOptions = useMemo(
    () => schemaPathOptions(schemas, columns),
    [schemas, columns],
  );
  const options = useMemo(
    () => filterPathOptions(allOptions, text),
    [allOptions, text],
  );
  // A trailing `.` / `[` is a path being typed: suggest, don't scold.
  const incomplete = /[.[]\s*$/.test(text);
  const invalid = parsed && !parsed.ok && !incomplete;
  const helper = error
    ? error
    : invalid
      ? `Invalid path · ${parsed.error}`
      : null;
  const preview = !helper ? previewText(sampleRow, parsed) : null;
  const showList = open && options.length > 0;

  const emit = (next) =>
    onChange?.(next, next.trim() ? parsePath(next, { columns }) : null);
  const pick = (o) => {
    emit(o.path);
    setActive(0);
    setOpen(!o.synthetic ? false : true);
  };

  return (
    <ClickAwayListener onClickAway={() => setOpen(false)}>
      <Box ref={anchor} sx={{ width: "100%" }}>
        <TextField
          autoFocus={autoFocus}
          value={text}
          onChange={(e) => {
            emit(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (showList && e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(options.length - 1, a + 1));
            } else if (showList && e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(0, a - 1));
            } else if (e.key === "Escape" && showList) {
              e.preventDefault();
              e.stopPropagation();
              setOpen(false);
            } else if (e.key === "Tab" && showList && !e.shiftKey) {
              e.preventDefault();
              pick(options[active] ?? options[0]);
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (showList && options[active]) pick(options[active]);
              else {
                setOpen(false);
                onEnter?.(text);
              }
            }
          }}
          placeholder={placeholder}
          size={size}
          fullWidth
          error={Boolean(helper)}
          helperText={
            helper ??
            (preview ? (
              <Typography
                component="span"
                typography="s3"
                color="text.secondary"
                noWrap
                sx={{ display: "block", fontFamily: MONO }}
              >
                {preview}
              </Typography>
            ) : undefined)
          }
          inputProps={{
            spellCheck: false,
            autoComplete: "off",
            "aria-label": "Path",
            "aria-autocomplete": "list",
            "aria-expanded": showList,
            style: { fontFamily: MONO },
          }}
        />
        <Popper
          open={showList}
          anchorEl={anchor.current}
          placement="bottom-start"
          style={{ zIndex: 1600, width: anchor.current?.offsetWidth }}
        >
          <Paper
            role="listbox"
            elevation={8}
            sx={{
              mt: 0.5,
              maxHeight: 320,
              overflow: "auto",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            {options.map((o, i) => (
              <Option
                key={o.path}
                option={o}
                active={i === active}
                onPick={pick}
              />
            ))}
          </Paper>
        </Popper>
      </Box>
    </ClickAwayListener>
  );
};

PathInput.propTypes = {
  autoFocus: PropTypes.bool,
  value: PropTypes.string,
  onChange: PropTypes.func,
  columns: PropTypes.arrayOf(PropTypes.string),
  schemas: PropTypes.object,
  sampleRow: PropTypes.object,
  placeholder: PropTypes.string,
  size: PropTypes.oneOf(["small", "medium"]),
  error: PropTypes.string,
  onEnter: PropTypes.func,
};

export default PathInput;
