// W5-8 Find (DE-11; W5-SCOPE §7 C4): "Find in this result" with `3 of 7`,
// ↑ ↓ (Enter / Shift+Enter) and Esc. One mount line in a host panel:
//
//   <FindBar scope=".MuiDrawer-paper" label="Find in this trace" />
//
// The panel is the closest ancestor of the mount that matches `scope` (the
// mount's parent when no scope is given). Cmd/Ctrl+F opens the bar only when
// focus is inside that panel. `onFilterRows(expressionText)` is optional:
// "Filter rows with this" is shown only when the host can apply it.
import React, { useEffect, useLayoutEffect, useRef } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  InputBase,
  Paper,
  Tooltip,
  Typography,
  Button,
} from "@mui/material";
import Iconify from "src/components/iconify";
import useFindInPanel from "./useFindInPanel";
import { countLabel, filterExpressionFor } from "./findModel";

export default function FindBar({
  scope,
  label = "Find in this result",
  primaryInput,
  onFilterRows,
}) {
  const anchorRef = useRef(null);
  const containerRef = useRef(null);
  const inputRef = useRef(null);
  useLayoutEffect(() => {
    const a = anchorRef.current;
    containerRef.current = a
      ? scope
        ? a.closest(scope)
        : a.parentElement
      : null;
  });
  const find = useFindInPanel({ containerRef });
  const { open, query, setQuery, matchCount, activeIndex, next, prev, close } =
    find;

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
      inputRef.current?.select?.();
    }
  }, [open]);

  const onKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (e.shiftKey) prev();
      else next();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  };

  return (
    <>
      <span ref={anchorRef} hidden data-search-skip="" />
      {open && (
        // A zero-height sticky strip: the bar floats over the panel's top
        // right while it scrolls, and pushes no content down.
        <Box
          data-search-skip=""
          sx={{
            position: "sticky",
            top: 8,
            zIndex: 20,
            height: 0,
            display: "flex",
            justifyContent: "flex-end",
            pr: 7,
          }}
        >
          <Paper
            data-search-skip=""
            data-testid="find-bar"
            elevation={4}
            role="search"
            aria-label={label}
            sx={{
              alignSelf: "flex-start",
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              pl: 1.25,
              pr: 0.5,
              py: 0.25,
              border: "1px solid",
              borderColor: "divider",
              // Never wider than the panel (M7: with "Filter rows with
              // this" the bar grew past the drawer's left edge).
              maxWidth: "100%",
              minWidth: 0,
            }}
          >
            <Iconify
              icon="mdi:magnify"
              width={16}
              sx={{ color: "text.secondary" }}
            />
            <InputBase
              inputRef={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={label}
              inputProps={{ "aria-label": label }}
              sx={{
                width: onFilterRows ? 150 : 220,
                minWidth: 80,
                flexShrink: 1,
                fontSize: 13,
              }}
            />
            <Typography
              variant="caption"
              color="text.secondary"
              data-testid="find-count"
              sx={{ minWidth: 64, textAlign: "right", whiteSpace: "nowrap" }}
            >
              {countLabel(activeIndex, matchCount, query)}
            </Typography>
            <Tooltip title="Previous (Shift+Enter)">
              <span>
                <IconButton
                  size="small"
                  aria-label="Previous match"
                  disabled={!matchCount}
                  onClick={prev}
                >
                  <Iconify icon="mdi:chevron-up" width={18} />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Next (Enter)">
              <span>
                <IconButton
                  size="small"
                  aria-label="Next match"
                  disabled={!matchCount}
                  onClick={next}
                >
                  <Iconify icon="mdi:chevron-down" width={18} />
                </IconButton>
              </span>
            </Tooltip>
            {onFilterRows && query.trim() && (
              <Button
                size="small"
                onClick={() =>
                  onFilterRows(filterExpressionFor(query, primaryInput))
                }
                sx={{ whiteSpace: "nowrap" }}
              >
                Filter rows with this
              </Button>
            )}
            <Box sx={{ width: 4 }} />
            <Tooltip title="Close (Esc)">
              <IconButton size="small" aria-label="Close find" onClick={close}>
                <Iconify icon="mingcute:close-line" width={16} />
              </IconButton>
            </Tooltip>
          </Paper>
        </Box>
      )}
    </>
  );
}

FindBar.propTypes = {
  scope: PropTypes.string,
  label: PropTypes.string,
  primaryInput: PropTypes.string,
  onFilterRows: PropTypes.func,
};
