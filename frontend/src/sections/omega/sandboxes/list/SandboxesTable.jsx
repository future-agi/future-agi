/* eslint-disable react-refresh/only-export-components */
import React, { useId, useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { useNavigate } from "react-router-dom";

import CopyIdButton from "../components/CopyIdButton";
import RelativeTime from "../components/RelativeTime";
import { RuntimeChip, StateChip, QuietTag } from "../components/SandboxStatus";
import {
  EMPTY,
  formatExactTime,
  formatLimits,
  runtimeLabel,
  shortId,
} from "../lib/format";
import { sandboxPath } from "../lib/links";
import useNow from "../lib/useNow";
import {
  SANDBOX_SORTS,
  SORT_LABEL_SX,
  nextSort,
  sortSandboxes,
} from "./listView";

// The state chip gets room for its longest label ("Needs recovery"); the size
// and time columns wrap a longer value ("0.5 CPU · 1536 MiB", "3 h 12 min
// ago") onto a second line; the image, with where it runs beneath it, takes
// what is left.
const COLUMNS = [
  { label: "Name", width: "22%", sort: "name" },
  { label: "State", width: 148, sort: "state" },
  { label: "Image · Runtime" },
  { label: "CPU · Memory", width: 124 },
  { label: "Last activity", width: 112, sort: "lastActivityAt" },
  { label: "Created", width: 104, sort: "createdAt" },
  { label: "Hibernates", width: 116, sort: "hibernatesAt" },
];

// The list's width at a 1200px window beside the sidebar. A list narrower
// than this (a docked side panel) shows cards, whatever the window's width.
const TABLE_MIN_WIDTH = 968;

/** The element's own width, or null until measured (0 = not laid out). */
function useElementWidth(ref) {
  const [width, setWidth] = useState(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    setWidth(element.getBoundingClientRect().width);
    const observer = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/**
 * True when the list shows cards: below 1200px, where the columns do not fit
 * without cutting values, or when the element `ref` holds is narrower than
 * the table needs (a docked side panel can narrow it at any window width).
 */
export function useCardLayout(ref) {
  const theme = useTheme();
  const narrowWindow = useMediaQuery(theme.breakpoints.down("lg"));
  const width = useElementWidth(ref);
  return narrowWindow || (width > 0 && width < TABLE_MIN_WIDTH);
}

const mono = { fontFamily: "monospace", fontSize: 12 };

function NameCell({ sandbox, copy = true }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
          noWrap
          title={sandbox.name}
        >
          {sandbox.name}
        </Typography>
        {sandbox.origin === "agent_run" && <QuietTag>Agent run</QuietTag>}
        {sandbox.origin === "restored" && <QuietTag>Restored</QuietTag>}
      </Box>
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 0.25, minWidth: 0 }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={mono}
          noWrap
          title={sandbox.id}
          data-testid="sandbox-row-id"
        >
          {shortId(sandbox.id, 10)}
        </Typography>
        {copy && <CopyIdButton value={sandbox.id} />}
      </Box>
    </Box>
  );
}

NameCell.propTypes = {
  sandbox: PropTypes.object.isRequired,
  /** The copy button; a card shows its own beside the card. */
  copy: PropTypes.bool,
};

const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

/**
 * A card's times as exact instants with the time zone, the ones the card
 * shows: "Last active 11 Nov 2026, 21:04:10 UTC", "Created …" and, for an
 * active sandbox, "Hibernates …".
 */
function exactTimes(sandbox) {
  const times = [
    ["Last active", sandbox.lastActivityAt],
    ["Created", sandbox.createdAt],
  ];
  if (sandbox.state === "active" && sandbox.hibernatesAt) {
    times.push(["Hibernates", sandbox.hibernatesAt]);
  }
  return times
    .map(([label, iso]) => [label, formatExactTime(iso)])
    .filter(([, exact]) => exact !== EMPTY)
    .map(([label, exact]) => `${label} ${exact}`);
}

function SandboxCard({ sandbox, now, onOpen }) {
  const active = sandbox.state === "active";
  // The times inside the button answer hover only; the button itself carries
  // them for a screen reader, and shows them on keyboard focus.
  const timesId = useId();
  const times = exactTimes(sandbox);
  return (
    <Box sx={{ position: "relative" }}>
      <Tooltip
        describeChild
        disableHoverListener
        disableTouchListener
        arrow
        title={times.map((line) => (
          <Box component="span" key={line} sx={{ display: "block" }}>
            {line}
          </Box>
        ))}
      >
        <Box
          component="button"
          type="button"
          onClick={onOpen}
          aria-describedby={timesId}
          data-testid="sandbox-row"
          data-sandbox-id={sandbox.id}
          sx={{
            all: "unset",
            boxSizing: "border-box",
            width: "100%",
            cursor: "pointer",
            display: "flex",
            flexDirection: "column",
            gap: 1,
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "background.paper",
            "&:hover, &:focus-visible": { borderColor: "text.disabled" },
            "&:focus-visible": {
              outline: (theme) => `2px solid ${theme.palette.border.focus}`,
              outlineOffset: -2,
            },
          }}
        >
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              gap: 1,
              minWidth: 0,
            }}
          >
            <NameCell sandbox={sandbox} copy={false} />
            <StateChip state={sandbox.state} />
          </Box>
          <Box
            sx={{
              display: "flex",
              flexWrap: "wrap",
              gap: 1,
              alignItems: "center",
            }}
          >
            <RuntimeChip provider={sandbox.provider} />
            {sandbox.image && (
              <Typography typography="s2" color="text.secondary" sx={mono}>
                {sandbox.image}
              </Typography>
            )}
          </Box>
          {/* Room on the right for Copy id, which sits over the card's corner. */}
          <Typography typography="s2" color="text.secondary" sx={{ pr: 10 }}>
            {sandbox.limits ? `${formatLimits(sandbox.limits)} · ` : ""}
            <RelativeTime
              prefix="active "
              iso={sandbox.lastActivityAt}
              now={now}
            />
            {" · "}
            <RelativeTime prefix="created " iso={sandbox.createdAt} now={now} />
            {active && sandbox.hibernatesAt ? " · " : null}
            {active && sandbox.hibernatesAt ? (
              <RelativeTime
                prefix="hibernates "
                iso={sandbox.hibernatesAt}
                now={now}
                future
              />
            ) : null}
          </Typography>
        </Box>
      </Tooltip>
      <CopyIdButton
        value={sandbox.id}
        labelled
        sx={{ position: "absolute", right: 8, bottom: 8 }}
      />
      <Box component="span" id={timesId} sx={visuallyHidden}>
        {times.join("; ")}
      </Box>
    </Box>
  );
}

SandboxCard.propTypes = {
  sandbox: PropTypes.object.isRequired,
  now: PropTypes.number.isRequired,
  onOpen: PropTypes.func.isRequired,
};

function HeaderCell({ column, sort, onSort }) {
  const active = Boolean(column.sort) && sort?.key === column.sort;
  return (
    <TableCell
      sortDirection={active ? sort.direction : false}
      sx={{
        typography: "s2",
        fontWeight: 500,
        color: "text.secondary",
        bgcolor: "background.neutral",
        borderColor: "divider",
        width: column.width,
        py: 1,
      }}
    >
      {column.sort ? (
        <TableSortLabel
          active={active}
          direction={active ? sort.direction : SANDBOX_SORTS[column.sort].first}
          onClick={() => onSort(nextSort(sort, column.sort))}
          sx={SORT_LABEL_SX}
          data-testid={`sort-${column.sort}`}
        >
          {column.label}
        </TableSortLabel>
      ) : (
        column.label
      )}
    </TableCell>
  );
}

HeaderCell.propTypes = {
  column: PropTypes.shape({
    label: PropTypes.string.isRequired,
    width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    sort: PropTypes.string,
  }).isRequired,
  sort: PropTypes.shape({ key: PropTypes.string, direction: PropTypes.string }),
  /** Called with the sort a click on this heading asks for. */
  onSort: PropTypes.func.isRequired,
};

function SandboxRows({ sandboxes, now, onOpen: open, sort, onSort }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        // Clips the corners without becoming a scroll box, so the header
        // stays at the top while the page scrolls.
        overflow: "clip",
      }}
    >
      <Table size="small" stickyHeader sx={{ tableLayout: "fixed" }}>
        <TableHead>
          <TableRow>
            {COLUMNS.map((column) => (
              <HeaderCell
                key={column.label}
                column={column}
                sort={sort}
                onSort={onSort}
              />
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {sandboxes.map((sandbox) => (
            <TableRow
              key={sandbox.id}
              hover
              tabIndex={0}
              data-testid="sandbox-row"
              data-sandbox-id={sandbox.id}
              onClick={() => open(sandbox)}
              onKeyDown={(event) => {
                if (event.key === "Enter") open(sandbox);
              }}
              sx={{
                cursor: "pointer",
                "& td": { borderColor: "divider", py: 1.25 },
                "&:focus-visible": {
                  outline: (theme) => `2px solid ${theme.palette.border.focus}`,
                  outlineOffset: -2,
                },
              }}
            >
              <TableCell>
                <NameCell sandbox={sandbox} />
              </TableCell>
              <TableCell>
                <StateChip state={sandbox.state} />
              </TableCell>
              <TableCell sx={{ color: "text.secondary" }}>
                <Box
                  component="span"
                  sx={{
                    ...mono,
                    display: "block",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={sandbox.image || ""}
                >
                  {sandbox.image || EMPTY}
                </Box>
                {sandbox.provider && (
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    data-testid="sandbox-runtime"
                    noWrap
                  >
                    {runtimeLabel(sandbox.provider.kind)}
                  </Typography>
                )}
              </TableCell>
              {/* A longer value wraps inside its own column. */}
              <TableCell
                data-testid="sandbox-limits"
                sx={{ typography: "s1", color: "text.primary" }}
              >
                {formatLimits(sandbox.limits)}
              </TableCell>
              <TableCell sx={{ typography: "s1", color: "text.secondary" }}>
                <RelativeTime
                  iso={sandbox.lastActivityAt}
                  now={now}
                  testId="sandbox-last-activity"
                  focusable
                />
              </TableCell>
              <TableCell sx={{ typography: "s1", color: "text.secondary" }}>
                <RelativeTime
                  iso={sandbox.createdAt}
                  now={now}
                  testId="sandbox-created"
                  focusable
                />
              </TableCell>
              <TableCell
                sx={{ typography: "s1", color: "text.secondary" }}
                data-testid="sandbox-hibernates"
              >
                {sandbox.state === "active" ? (
                  <RelativeTime
                    iso={sandbox.hibernatesAt}
                    now={now}
                    future
                    focusable
                  />
                ) : (
                  EMPTY
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

SandboxRows.propTypes = {
  sandboxes: PropTypes.arrayOf(PropTypes.object).isRequired,
  now: PropTypes.number.isRequired,
  onOpen: PropTypes.func.isRequired,
  sort: PropTypes.object,
  onSort: PropTypes.func.isRequired,
};

/**
 * The sandboxes as table rows, or as cards that show every value when the
 * columns would not fit. The sort is the page's (null keeps the API's order),
 * so it outlives a filter that matches nothing and a switch between table and
 * cards. The page names the layout, since its toolbar offers Sort by with the
 * cards; shown alone, the list measures itself.
 */
export default function SandboxesTable({
  sandboxes,
  sort = null,
  onSort,
  cards,
}) {
  const listRef = useRef(null);
  const ownLayout = useCardLayout(listRef);
  const narrow = cards ?? ownLayout;
  const navigate = useNavigate();
  const now = useNow();
  const rows = sortSandboxes(sandboxes, sort);
  const open = (sandbox) => navigate(sandboxPath(sandbox.id));

  return (
    <Box ref={listRef} data-testid="sandboxes-list">
      {narrow ? (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {rows.map((sandbox) => (
            <SandboxCard
              key={sandbox.id}
              sandbox={sandbox}
              now={now}
              onOpen={() => open(sandbox)}
            />
          ))}
        </Box>
      ) : (
        <SandboxRows
          sandboxes={rows}
          now={now}
          onOpen={open}
          sort={sort}
          onSort={onSort}
        />
      )}
    </Box>
  );
}

SandboxesTable.propTypes = {
  sandboxes: PropTypes.arrayOf(PropTypes.object).isRequired,
  sort: PropTypes.shape({ key: PropTypes.string, direction: PropTypes.string }),
  /** Called with the new sort, `{ key, direction }`. */
  onSort: PropTypes.func.isRequired,
  /** Cards instead of the table; measured here when the page doesn't say. */
  cards: PropTypes.bool,
};
