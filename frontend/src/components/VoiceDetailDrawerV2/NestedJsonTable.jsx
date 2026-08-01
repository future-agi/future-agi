import React, { useMemo, useState, useTransition } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, alpha } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  canonicalEntries,
  canonicalKeys,
  fuzzyThreshold,
  levenshtein,
  splitWords,
  tokenizeQuery,
  tokenMatchesLeaf,
} from "src/utils/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Shared recursive key/value JSON viewer
//
// Same visual language as the trace drawer's AttributesCard
// (SpanDetailPane.jsx) so the voice drawer's Attributes / Messages /
// Logs detail panels all look identical. Features:
//
//   • Collapsible nested objects and arrays — no pre-flattening
//   • Deep search across keys and primitive values, with inline match
//     highlights
//   • Click-to-copy key-value pair
//   • Stable, monospace Key | Value layout
// ─────────────────────────────────────────────────────────────────────────────

const collectStrings = (val, out) => {
  if (val == null) return;
  if (typeof val === "string") {
    out.push(val.toLowerCase());
    return;
  }
  if (typeof val === "number" || typeof val === "boolean") {
    out.push(String(val).toLowerCase());
    return;
  }
  if (Array.isArray(val)) {
    val.forEach((v) => collectStrings(v, out));
    return;
  }
  if (typeof val === "object") {
    // De-dupe mixed-key local objects so a single field doesn't get matched
    // twice in search.
    canonicalEntries(val).forEach(([k, v]) => {
      out.push(String(k).toLowerCase());
      collectStrings(v, out);
    });
  }
};

export const deepMatchTokens = (val, tokens) => {
  if (!tokens || tokens.length === 0) return true;
  const haystack = [];
  collectStrings(val, haystack);
  // Lazy — only materialised if any token falls through to the fuzzy
  // path, so exact-substring queries stay zero-cost.
  let words = null;
  return tokens.every((tok) => {
    if (haystack.some((s) => s.includes(tok))) return true;
    const thr = fuzzyThreshold(tok);
    if (thr === 0) return false;
    if (words === null) {
      words = [];
      haystack.forEach((s) => splitWords(s).forEach((w) => words.push(w)));
    }
    return words.some(
      (w) =>
        Math.abs(w.length - tok.length) <= thr && levenshtein(tok, w) <= thr,
    );
  });
};

// Back-compat wrapper. `VoiceLogsView` passes a single query string and
// expects "all tokens match" semantics, same as the attributes filter.
export const deepMatch = (val, q) => deepMatchTokens(val, tokenizeQuery(q));

// Flattens a JSON-like tree into an array of primitive leaves for the
// flat-match search mode. Each entry is `{ path, value, pathLower,
// valueLower }` where `path` uses dotted object keys and `[i]` for
// array indices. Empty containers are themselves emitted as leaves so
// a search that matches only their path (e.g. `extras`) still returns
// a hit.
export const flattenLeaves = (root) => {
  const out = [];
  const push = (path, value) => {
    const pathStr = String(path);
    let valueStr;
    if (value == null || typeof value === "object") valueStr = "";
    else if (typeof value === "string") valueStr = value;
    else valueStr = String(value);
    const pathLower = pathStr.toLowerCase();
    const valueLower = valueStr.toLowerCase();
    // `words` is what the Levenshtein fallback compares against —
    // pre-split once per leaf so fuzzy filtering isn't re-tokenising on
    // every keystroke.
    out.push({
      path: pathStr,
      value,
      pathLower,
      valueLower,
      words: [...splitWords(pathLower), ...splitWords(valueLower)],
    });
  };
  const walk = (val, path) => {
    if (val == null || typeof val !== "object") {
      push(path, val);
      return;
    }
    if (Array.isArray(val)) {
      if (val.length === 0) {
        push(path, val);
        return;
      }
      val.forEach((item, i) => {
        walk(item, path ? `${path}[${i}]` : `[${i}]`);
      });
      return;
    }
    const entries = canonicalEntries(val);
    if (entries.length === 0) {
      push(path, val);
      return;
    }
    entries.forEach(([k, child]) => {
      walk(child, path ? `${path}.${k}` : String(k));
    });
  };
  walk(root, "");
  return out;
};

// Highlights every occurrence of any search token. Tokens are matched
// independently (order-independent, case-insensitive substring) so the
// fuzzy "all tokens must appear" filter is reflected visually.
const Highlight = ({ text, tokens }) => {
  if (!tokens || tokens.length === 0 || text == null) return text;
  const str = String(text);
  const lower = str.toLowerCase();
  const ranges = [];
  tokens.forEach((tok) => {
    if (!tok) return;
    let from = 0;
    while (from < lower.length) {
      const idx = lower.indexOf(tok, from);
      if (idx === -1) break;
      ranges.push([idx, idx + tok.length]);
      from = idx + tok.length;
    }
  });
  if (ranges.length === 0) return str;
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  ranges.forEach(([s, e]) => {
    const last = merged[merged.length - 1];
    if (last && s <= last[1]) {
      last[1] = Math.max(last[1], e);
    } else {
      merged.push([s, e]);
    }
  });
  const parts = [];
  let cursor = 0;
  merged.forEach(([s, e], i) => {
    if (s > cursor) parts.push(str.slice(cursor, s));
    parts.push(
      <Box
        key={`hl-${i}`}
        component="span"
        sx={{
          bgcolor: (theme) => alpha(theme.palette.warning.main, 0.28),
          borderRadius: "2px",
          px: "1px",
        }}
      >
        {str.slice(s, e)}
      </Box>,
    );
    cursor = e;
  });
  if (cursor < str.length) parts.push(str.slice(cursor));
  return <>{parts}</>;
};

Highlight.propTypes = {
  text: PropTypes.any,
  tokens: PropTypes.arrayOf(PropTypes.string),
};

// Primitive (and degenerate empty-container) renderer. Shared between
// the tree `ValueCell` and the flat-match `FlatMatchRow` so both modes
// render the same monospace "quoted string / plain number" style.
//
// `overflowWrap: "anywhere"` wraps at natural break points first (spaces,
// punctuation) and falls back to mid-word breaks only when a token
// genuinely overflows, avoiding the "Microsoft" one-char-per-line glitch
// at deep nesting where the value column is narrow.
const PrimitiveValue = ({ value, tokens }) => {
  if (value == null) {
    return (
      <Typography
        component="span"
        sx={{
          fontSize: 11,
          color: "text.disabled",
          fontFamily: "monospace",
        }}
      >
        null
      </Typography>
    );
  }
  if (typeof value === "object") {
    const isArr = Array.isArray(value);
    return (
      <Typography
        component="span"
        sx={{ fontSize: 11, color: "text.disabled", fontStyle: "italic" }}
      >
        empty {isArr ? "array" : "object"}
      </Typography>
    );
  }
  // Strings get quotes and the fixed `#b5520a` colour used by the trace
  // drawer's AttributesCard (SpanDetailPane.jsx) so the two views read
  // identically across light and dark mode.
  const display = typeof value === "string" ? `"${value}"` : String(value);
  return (
    <Typography
      component="span"
      sx={{
        fontSize: 11,
        fontFamily: "monospace",
        color: typeof value === "string" ? "#b5520a" : "text.primary",
        overflowWrap: "anywhere",
        whiteSpace: "pre-wrap",
      }}
    >
      <Highlight text={display} tokens={tokens} />
    </Typography>
  );
};

PrimitiveValue.propTypes = {
  value: PropTypes.any,
  tokens: PropTypes.arrayOf(PropTypes.string),
};

// Renders a value. Primitives render inline; objects and arrays get a
// "X items" chevron that expands to a left-indented list of child rows.
const ValueCell = ({ value, tokens }) => {
  const [expanded, setExpanded] = useState(false);
  // Expanding a huge subtree (e.g. `raw_log` with thousands of nested
  // rows) blocks the main thread long enough that a user's second click
  // lands on the now-open tree and re-closes it. `useTransition` yields
  // back to event handling during the render, and `isPending` lets us
  // swallow the second click until the first toggle is on screen.
  const [isPending, startToggleTransition] = useTransition();
  const toggleExpanded = () => {
    if (isPending) return;
    startToggleTransition(() => setExpanded((v) => !v));
  };

  const isObj =
    value != null && typeof value === "object" && !Array.isArray(value);
  const isArr = Array.isArray(value);
  const count = isArr ? value.length : isObj ? canonicalKeys(value).length : 0;

  if ((isObj || isArr) && count > 0) {
    return (
      <Box sx={{ minWidth: 0 }}>
        <Box
          onClick={(e) => {
            e.stopPropagation();
            toggleExpanded();
          }}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.25,
            cursor: isPending ? "wait" : "pointer",
            userSelect: "none",
            opacity: isPending ? 0.6 : 1,
            "&:hover": { opacity: isPending ? 0.6 : 0.75 },
          }}
        >
          <Iconify
            icon={expanded ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={12}
            sx={{ color: "text.disabled" }}
          />
          <Typography
            component="span"
            sx={{
              fontSize: 10.5,
              color: "text.secondary",
              fontFamily: "monospace",
            }}
          >
            {isArr ? `array · ${count}` : `object · ${count} keys`}
            {isPending ? " · …" : ""}
          </Typography>
        </Box>
        {expanded && (
          <Box
            sx={{
              mt: 0.25,
              ml: 0.75,
              pl: 0.75,
              borderLeft: "1px solid",
              borderColor: "divider",
            }}
          >
            {(isArr
              ? value.map((v, i) => [String(i), v])
              : canonicalEntries(value)
            ).map(([k, v]) => (
              <NestedRow
                key={k}
                path={k}
                value={v}
                tokens={tokens}
                defaultOpen={false}
              />
            ))}
          </Box>
        )}
      </Box>
    );
  }

  return <PrimitiveValue value={value} tokens={tokens} />;
};

ValueCell.propTypes = {
  value: PropTypes.any,
  tokens: PropTypes.arrayOf(PropTypes.string),
};

// One nested row: `path` column on the left, `value` column on the
// right. Clicking an object/array row toggles its children.
//
// The value column holds a hard `minWidth` so deeply-nested primitives
// never collapse to a few pixels wide. When the row's intrinsic width
// exceeds the pane, the outer AttributesTable scroll container picks
// up horizontal scroll instead of the text wrapping character-by-line.
const VALUE_MIN_WIDTH = 220;

const NestedRow = ({ path, value, tokens, defaultOpen }) => {
  const [open, setOpen] = useState(!!defaultOpen);
  const isComplex = value != null && typeof value === "object";
  // See ValueCell — expanding a huge branch is heavy, so do it inside a
  // transition and ignore rapid second clicks until the expand paints.
  const [isPending, startToggleTransition] = useTransition();
  const toggleOpen = () => {
    if (!isComplex || isPending) return;
    startToggleTransition(() => setOpen((v) => !v));
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 0.5,
        py: 0.15,
        px: 0.25,
        borderRadius: "2px",
        "&:hover": isComplex ? { bgcolor: "action.hover" } : {},
      }}
    >
      <Typography
        onClick={toggleOpen}
        sx={{
          fontSize: 10.5,
          fontWeight: 500,
          fontFamily: "monospace",
          color: "text.secondary",
          minWidth: 110,
          maxWidth: 180,
          flexShrink: 0,
          cursor: isComplex ? (isPending ? "wait" : "pointer") : "default",
          overflowWrap: "anywhere",
        }}
      >
        <Highlight text={path} tokens={tokens} />
      </Typography>
      <Box sx={{ flex: 1, minWidth: VALUE_MIN_WIDTH }}>
        {isComplex ? (
          <Box>
            <Box
              onClick={toggleOpen}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.25,
                cursor: isPending ? "wait" : "pointer",
                opacity: isPending ? 0.6 : 1,
                "&:hover": { opacity: isPending ? 0.6 : 0.75 },
              }}
            >
              <Iconify
                icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={12}
                sx={{ color: "text.disabled" }}
              />
              <Typography
                component="span"
                sx={{
                  fontSize: 10.5,
                  color: "text.secondary",
                  fontFamily: "monospace",
                }}
              >
                {Array.isArray(value)
                  ? `array · ${value.length}`
                  : `object · ${canonicalKeys(value).length} keys`}
                {isPending ? " · …" : ""}
              </Typography>
            </Box>
            {open && (
              <Box
                sx={{
                  mt: 0.25,
                  ml: 0.75,
                  pl: 0.75,
                  borderLeft: "1px solid",
                  borderColor: "divider",
                }}
              >
                {(Array.isArray(value)
                  ? value.map((v, i) => [String(i), v])
                  : canonicalEntries(value)
                ).map(([k, v]) => (
                  <NestedRow
                    key={k}
                    path={k}
                    value={v}
                    tokens={tokens}
                    defaultOpen={false}
                  />
                ))}
              </Box>
            )}
          </Box>
        ) : (
          <ValueCell value={value} tokens={tokens} />
        )}
      </Box>
    </Box>
  );
};

NestedRow.propTypes = {
  path: PropTypes.string,
  value: PropTypes.any,
  tokens: PropTypes.arrayOf(PropTypes.string),
  defaultOpen: PropTypes.bool,
};

// Single row in flat-match mode: dotted path tightly followed by the
// primitive value. Per-row flex instead of a shared grid so the value
// sits immediately next to the path regardless of how long the path
// is — no reserved column width means no dead whitespace between them.
export const FlatMatchRow = ({ path, value, tokens }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "baseline",
      columnGap: 0.75,
      py: 0.15,
      px: 0.25,
      borderRadius: "2px",
    }}
  >
    <Typography
      component="span"
      sx={{
        fontSize: 10.5,
        fontWeight: 500,
        fontFamily: "monospace",
        color: "text.secondary",
        overflowWrap: "anywhere",
        flexShrink: 0,
      }}
    >
      <Highlight text={path || "value"} tokens={tokens} />
    </Typography>
    <Box sx={{ flex: 1, minWidth: VALUE_MIN_WIDTH }}>
      <PrimitiveValue value={value} tokens={tokens} />
    </Box>
  </Box>
);

FlatMatchRow.propTypes = {
  path: PropTypes.string,
  value: PropTypes.any,
  tokens: PropTypes.arrayOf(PropTypes.string),
};

// Hard cap on flat-match results. A payload with a very common token
// (e.g. `error` across a 20k-row `raw_log`) can match thousands of
// leaves — rendering them all would tank the drawer. The footer tells
// the user to narrow their search instead.
const MAX_FLAT_RESULTS = 500;

/**
 * Top-level renderer. Accepts any JSON-like object and renders it as a
 * nested, searchable table.
 *
 * `searchQuery` — optional external query (e.g. from a parent search
 *   box). When non-empty, the tree is replaced by a flat list of
 *   matching primitive leaves (path + value, highlighted).
 * `emptyMessage` — what to show when there are no entries.
 */
const NestedJsonTable = ({
  data,
  searchQuery = "",
  emptyMessage = "No data",
  header,
}) => {
  const parsed = useMemo(() => {
    if (data == null) return null;
    if (typeof data === "string") {
      try {
        return JSON.parse(data);
      } catch {
        return data;
      }
    }
    return data;
  }, [data]);

  const entries = useMemo(() => {
    if (parsed == null) return [];
    if (typeof parsed !== "object") return [["value", parsed]];
    if (Array.isArray(parsed)) return parsed.map((v, i) => [String(i), v]);
    // Strip camelCase aliases that may exist in legacy objects to every
    // snake_case key so each field renders once, not twice.
    return canonicalEntries(parsed);
  }, [parsed]);

  const tokens = useMemo(() => tokenizeQuery(searchQuery), [searchQuery]);
  const hasQuery = tokens.length > 0;

  // Flat leaves are only materialised when a query is active. Walking
  // the whole structure has a cost for large payloads (`raw_log` can
  // hold tens of thousands of nested nodes) — doing it lazily keeps
  // the no-query path zero-cost.
  const flatLeaves = useMemo(() => {
    if (!hasQuery || parsed == null) return [];
    return flattenLeaves(parsed);
  }, [parsed, hasQuery]);

  const filteredFlat = useMemo(() => {
    if (!hasQuery) return [];
    return flatLeaves.filter((l) =>
      tokens.every((tok) =>
        tokenMatchesLeaf(tok, l.pathLower, l.valueLower, l.words),
      ),
    );
  }, [flatLeaves, tokens, hasQuery]);

  if (entries.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          py: 2,
        }}
      >
        <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
          {emptyMessage}
        </Typography>
      </Box>
    );
  }

  if (hasQuery) {
    if (filteredFlat.length === 0) {
      return (
        <Stack gap={0.25} sx={{ minWidth: 0 }}>
          {header}
          <Typography
            sx={{
              fontSize: 10.5,
              color: "text.disabled",
              fontStyle: "italic",
              px: 0.5,
              py: 0.5,
            }}
          >
            No matches for &ldquo;{tokens.join(" ")}&rdquo;
          </Typography>
        </Stack>
      );
    }
    const capped = filteredFlat.slice(0, MAX_FLAT_RESULTS);
    const overflow = filteredFlat.length - capped.length;
    return (
      <Stack gap={0.25} sx={{ minWidth: 0 }}>
        {header}
        {capped.map((leaf, i) => (
          <FlatMatchRow
            key={`${leaf.path}__${i}`}
            path={leaf.path}
            value={leaf.value}
            tokens={tokens}
          />
        ))}
        {overflow > 0 && (
          <Typography
            sx={{
              fontSize: 10.5,
              color: "text.disabled",
              fontStyle: "italic",
              px: 0.5,
              py: 0.5,
            }}
          >
            · … {overflow} more matches, refine your query ·
          </Typography>
        )}
      </Stack>
    );
  }

  return (
    <Stack gap={0.25} sx={{ minWidth: 0 }}>
      {header}
      {entries.map(([k, v]) => (
        <NestedRow
          key={k}
          path={k}
          value={v}
          tokens={tokens}
          defaultOpen={false}
        />
      ))}
    </Stack>
  );
};

NestedJsonTable.propTypes = {
  data: PropTypes.any,
  searchQuery: PropTypes.string,
  emptyMessage: PropTypes.string,
  header: PropTypes.node,
};

export default NestedJsonTable;
