import React, { useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import Skeleton from "@mui/material/Skeleton";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useNavigate, useSearchParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { KINDS, KIND_LABEL, TIERS, hubPaths } from "./constants";
import { useHubMe, useReviewQueue } from "./api/queries";
import { formatStamp } from "./lib/decision";
import { timeAgo } from "./lib/format";
import { checkTone, statusMeta } from "./lib/status";
import { StatusChip } from "./components/StatusTimeline";
import { HubEmpty, HubError } from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";
import TierBadge from "./components/TierBadge";
import ToneChip from "./components/ToneChip";

// `any` is what a filter's pill says while it holds its default.
const FILTERS = [
  {
    key: "status",
    label: "Status",
    options: [
      ["in_review", statusMeta("in_review").label],
      ["check_error", statusMeta("check_error").label],
    ],
  },
  {
    key: "kind",
    label: "Kind",
    any: "Any kind",
    options: [["", "Any kind"], ...KINDS.map((k) => [k.value, k.label])],
  },
  {
    key: "tier",
    label: "Tier",
    any: "Any tier",
    options: [
      ["", "Any tier"],
      ...Object.entries(TIERS).map(([v, m]) => [v, m.label]),
    ],
  },
  {
    key: "update",
    label: "Type",
    any: "Any type",
    options: [
      ["", "New and updates"],
      ["0", "New entries"],
      ["1", "Updates"],
    ],
  },
  {
    key: "flags",
    label: "Flags",
    any: "Any flags",
    options: [
      ["", "With or without flags"],
      ["1", "Has flags"],
    ],
  },
  {
    key: "channel",
    label: "Channel",
    any: "Any channel",
    options: [
      ["", "Any channel"],
      ["cli", "CLI"],
      ["web", "Web upload"],
      ["github", "GitHub"],
    ],
  },
];

const DEFAULTS = Object.fromEntries(
  FILTERS.map((f) => [f.key, f.key === "status" ? "in_review" : ""]),
);

// The source a row came in through, named as the Channel filter names it.
const SOURCE = Object.fromEntries(
  FILTERS.find((f) => f.key === "channel").options,
);

const VISUALLY_HIDDEN = {
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

// Columns a phone has no room for; their facts move under the ref.
const WIDE_ONLY = { display: { xs: "none", md: "table-cell" } };
const NARROW_ONLY = { display: { md: "none" } };
// The tier badge under the ref is an inline-flex box; middle sets its word on
// the line of the text around it.
const FOLD_SX = { ...NARROW_ONLY, "& > span": { verticalAlign: "middle" } };
const LOCK_MS = 2 * 3600 * 1000;
// A table cell does not honour max-width, so the reviewer's name is cut short
// inside a block of its own. Narrower from md to lg: at 1024 px with the
// sidebar open the table has about 776 px.
const REVIEWER_SX = {
  maxWidth: { md: 160, lg: 200 },
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

// Below md the dashboard's 48 px mobile bar sticks to the top of the page, so
// the queue's header sticks under the bar instead of sliding behind it.
const TABLE_SX = {
  minWidth: 0,
  "& .MuiTableCell-stickyHeader": { top: { xs: 48, md: 0 } },
};

const time = (iso) => new Date(iso).getTime() || 0;
const TIER_ORDER = Object.keys(TIERS);

// Who holds the review lock while it holds (two hours from its start), or "".
function lockHolder(row) {
  const live =
    row.reviewer &&
    row.reviewStartedAt &&
    Date.now() - time(row.reviewStartedAt) < LOCK_MS;
  return live ? row.reviewer : "";
}

const SORTS = {
  entry: {
    first: "asc",
    value: (row) => `${row.ref}@${row.version || ""}`,
    note: { asc: "By entry, A to Z", desc: "By entry, Z to A" },
  },
  kind: {
    first: "asc",
    value: (row) => KIND_LABEL[row.kind] || row.kind || "",
    note: { asc: "By kind, A to Z", desc: "By kind, Z to A" },
  },
  tier: {
    first: "asc",
    value: (row) => {
      const index = TIER_ORDER.indexOf(row.tier);
      return index < 0 ? TIER_ORDER.length : index;
    },
    note: { asc: "Official first", desc: "Community first" },
  },
  waiting: {
    first: "desc",
    value: (row) => -time(row.createdAt),
    note: { asc: "Newest first", desc: "Oldest first" },
  },
  checks: {
    first: "desc",
    value: (row) => (row.status === "check_error" ? -1 : row.flags || 0),
    note: { asc: "Fewest flags first", desc: "Most flags first" },
  },
  // By who holds the review, as the Reviewer column shows it. Rows nobody has
  // started sort after the held ones, and before them from Z to A.
  reviewer: {
    first: "asc",
    value: (row) => {
      const holder = lockHolder(row);
      return holder ? `0${holder}` : "1";
    },
    note: { asc: "By reviewer, A to Z", desc: "By reviewer, Z to A" },
  },
};

const COLUMNS = [
  { key: "entry", label: "Entry" },
  { key: "kind", label: "Kind", sx: WIDE_ONLY },
  { key: "tier", label: "Tier", sx: WIDE_ONLY },
  { key: "waiting", label: "Waiting" },
  { key: "checks", label: "Checks" },
  { key: "reviewer", label: "Reviewer", sx: WIDE_ONLY },
];

function compare(a, b) {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

function sortRows(rows, sort, order) {
  const { value } = SORTS[sort];
  const sign = order === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort(
      (a, b) => sign * compare(value(a.row), value(b.row)) || a.index - b.index,
    )
    .map((item) => item.row);
}

function waitText(iso) {
  const ago = timeAgo(iso);
  return ago === "just now" ? "under 1 min" : ago.replace(" ago", "");
}

function pillText(filter, value) {
  const label =
    filter.options.find(([option]) => option === value)?.[1] || value;
  if (value === DEFAULTS[filter.key]) return filter.any || label;
  return `${filter.label}: ${label}`;
}

function Row({ row, meEmail, onOpen }) {
  const href = hubPaths.reviewItem(row.id);
  const [publisher, ...path] = String(row.ref).split("/");
  const kind = KIND_LABEL[row.kind] || row.kind || "Unknown kind";
  const holder = lockHolder(row);
  const mine = Boolean(holder) && holder === meEmail;
  const reviewing = holder
    ? `${mine ? "You" : holder}, started ${timeAgo(row.reviewStartedAt)}`
    : "";
  // A Cmd, Ctrl or middle click anywhere on the row opens the review in a new
  // tab, the way it does on the ref link.
  const openInTab = () => window.open(href, "_blank", "noopener");
  return (
    <TableRow
      hover
      data-testid="hub-queue-row"
      data-ref={row.ref}
      onClick={(event) => {
        if (event.target.closest("a")) return;
        if (event.metaKey || event.ctrlKey) openInTab();
        else onOpen(href);
      }}
      onAuxClick={(event) => {
        if (event.button === 1 && !event.target.closest("a")) openInTab();
      }}
      sx={{ cursor: "pointer" }}
    >
      <TableCell sx={{ minWidth: { md: 180, lg: 200 } }}>
        <Link
          component={RouterLink}
          href={href}
          aria-label={row.version ? `${row.ref}@${row.version}` : row.ref}
          underline="hover"
          color="text.primary"
          typography="s1"
          sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
        >
          <Box component="span" sx={{ color: "text.secondary" }}>
            {`${publisher}/`}
          </Box>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {path.join("/")}
          </Box>
          {row.version && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {`@${row.version}`}
            </Box>
          )}
        </Link>
        <Typography typography="s2" color="text.secondary">
          <Box component="span" sx={FOLD_SX}>
            {`${kind} · `}
            <TierBadge tier={row.tier} short quiet />
            {" · "}
          </Box>
          {`${row.isUpdate ? "Update" : "New entry"} · via ${SOURCE[row.channel] || row.channel}`}
          {holder && (
            <Box component="span" sx={NARROW_ONLY}>
              {mine
                ? " · you are reviewing it"
                : ` · being reviewed by ${holder}`}
            </Box>
          )}
        </Typography>
      </TableCell>
      <TableCell sx={{ ...WIDE_ONLY, whiteSpace: "nowrap" }}>{kind}</TableCell>
      <TableCell sx={WIDE_ONLY}>
        <TierBadge tier={row.tier} short quiet />
      </TableCell>
      <TableCell sx={{ whiteSpace: "nowrap" }}>
        <Box
          component="time"
          dateTime={row.createdAt}
          title={`Submitted ${formatStamp(row.createdAt)}`}
          sx={{ color: "text.primary" }}
        >
          {waitText(row.createdAt)}
        </Box>
      </TableCell>
      <TableCell sx={{ whiteSpace: "nowrap" }}>
        {row.status === "check_error" ? (
          <StatusChip status={row.status} />
        ) : row.flags > 0 ? (
          <ToneChip
            tone={checkTone("flag")}
            label={`${row.flags} flag${row.flags === 1 ? "" : "s"}`}
          />
        ) : (
          "No flags"
        )}
      </TableCell>
      <TableCell sx={WIDE_ONLY}>
        {holder ? (
          <Box title={reviewing} sx={REVIEWER_SX}>
            {reviewing}
          </Box>
        ) : (
          <Box
            component="span"
            sx={{ color: "text.tertiary", whiteSpace: "nowrap" }}
          >
            Not started
          </Box>
        )}
      </TableCell>
    </TableRow>
  );
}

Row.propTypes = {
  row: PropTypes.object.isRequired,
  meEmail: PropTypes.string,
  onOpen: PropTypes.func.isRequired,
};

function QueueSkeleton() {
  return (
    <Box
      data-testid="hub-loading"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      {[0, 1, 2, 3].map((index) => (
        <Skeleton key={index} variant="rounded" height={44} />
      ))}
    </Box>
  );
}

// Nothing arrives through the CLI channel until a customer can install a CLI
// with `omega hub` (OMEGA_PART HUB_CLI), so that filter value waits for it.
function visibleFilters(cliReleased) {
  if (cliReleased) return FILTERS;
  return FILTERS.map((f) =>
    f.key === "channel"
      ? { ...f, options: f.options.filter(([value]) => value !== "cli") }
      : f,
  );
}

// The review queue (SPEC §5.2): in_review, oldest first, with the filters
// of the spec. Each filter shows what it holds, and a narrowed one reads as
// "Kind: Agents" with Clear all beside the row. The queue is a table sorted
// by its column headers (the sort sits in the URL beside the filters); each
// row opens its review, and its ref is the link. A value the URL names that
// is not an offered option falls back to the default.
export default function HubReviewQueuePage() {
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const firstFilter = useRef(null);
  const me = useHubMe();
  const filters = visibleFilters(useOmegaPart(OMEGA_PART.HUB_CLI));
  const params = Object.fromEntries(
    filters.map((f) => {
      const fallback = DEFAULTS[f.key];
      const value = search.get(f.key) || fallback;
      return [
        f.key,
        f.options.some(([option]) => option === value) ? value : fallback,
      ];
    }),
  );
  const filtering = filters.some((f) => params[f.key] !== DEFAULTS[f.key]);
  const sortParam = search.get("sort");
  const sort = Object.keys(SORTS).includes(sortParam) ? sortParam : "waiting";
  const order = ["asc", "desc"].includes(search.get("order"))
    ? search.get("order")
    : SORTS[sort].first;
  const queue = useReviewQueue(params, Boolean(me.data?.isMaintainer));
  const rows = sortRows(queue.data?.results || [], sort, order);
  const set = (key, value) => {
    const next = new URLSearchParams(search);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearch(next, { replace: true });
  };
  const clearAll = () => {
    const next = new URLSearchParams(search);
    for (const filter of FILTERS) next.delete(filter.key);
    setSearch(next, { replace: true });
    firstFilter.current?.focus();
  };
  const sortBy = (key) => {
    const flipped = order === "asc" ? "desc" : "asc";
    const nextOrder = key === sort ? flipped : SORTS[key].first;
    const next = new URLSearchParams(search);
    if (key === "waiting" && nextOrder === "desc") {
      next.delete("sort");
      next.delete("order");
    } else {
      next.set("sort", key);
      next.set("order", nextOrder);
    }
    setSearch(next, { replace: true });
  };
  return (
    <MaintainerGate
      title="Review queue"
      documentTitle="Review queue"
      description="Submissions that passed the automated checks, oldest first. Nothing is listed until one of you approves it."
      testId="hub-review-queue"
    >
      <Box
        role="group"
        aria-label="Filter the queue"
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 1,
        }}
      >
        {filters.map((filter, index) => {
          const active = params[filter.key] !== DEFAULTS[filter.key];
          return (
            <TextField
              key={filter.key}
              select
              size="small"
              label={filter.label}
              value={params[filter.key]}
              onChange={(e) => set(filter.key, e.target.value)}
              inputRef={index === 0 ? firstFilter : undefined}
              InputLabelProps={{ shrink: true, sx: VISUALLY_HIDDEN }}
              SelectProps={{
                displayEmpty: true,
                renderValue: (value) => pillText(filter, value),
              }}
              data-active={active ? "true" : undefined}
              sx={{
                "& .MuiOutlinedInput-root": {
                  bgcolor: active ? "action.selected" : "background.paper",
                },
                "& .MuiOutlinedInput-notchedOutline": {
                  borderColor: active ? "text.secondary" : "divider",
                },
                "& .MuiSelect-select": { typography: "s1", py: 0.75 },
              }}
            >
              {filter.options.map(([value, label]) => (
                <MenuItem key={value || "any"} value={value}>
                  {label}
                </MenuItem>
              ))}
            </TextField>
          );
        })}
        {filtering && (
          <Button size="small" color="inherit" onClick={clearAll}>
            Clear all filters
          </Button>
        )}
      </Box>
      {queue.isLoading ? (
        <QueueSkeleton />
      ) : queue.isError ? (
        <HubError error={queue.error} onRetry={() => queue.refetch()} />
      ) : rows.length === 0 ? (
        filtering ? (
          <HubEmpty
            title="Nothing matches these filters"
            description="No submission waiting for review matches every filter above."
            action={
              <Button size="small" variant="outlined" onClick={clearAll}>
                Show the whole queue
              </Button>
            }
          />
        ) : (
          <HubEmpty
            title="The queue is empty"
            description="Every submission that passed its checks has a decision."
          />
        )
      ) : (
        <>
          <Box
            sx={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 1,
              flexWrap: "wrap",
            }}
          >
            <Typography
              typography="s1"
              color="text.primary"
              data-testid="hub-queue-count"
            >
              {`${queue.data.count} waiting${filtering ? " with these filters" : ""}`}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {SORTS[sort].note[order]}
            </Typography>
          </Box>
          <Table stickyHeader aria-label="Review queue" sx={TABLE_SX}>
            <TableHead>
              <TableRow>
                {COLUMNS.map((column) => (
                  <TableCell
                    key={column.key}
                    sortDirection={sort === column.key ? order : false}
                    sx={{ ...column.sx, whiteSpace: "nowrap" }}
                  >
                    {SORTS[column.key] ? (
                      <TableSortLabel
                        active={sort === column.key}
                        direction={
                          sort === column.key ? order : SORTS[column.key].first
                        }
                        onClick={() => sortBy(column.key)}
                      >
                        {column.label}
                        {sort === column.key && (
                          <Box component="span" sx={VISUALLY_HIDDEN}>
                            {order === "desc"
                              ? "sorted descending"
                              : "sorted ascending"}
                          </Box>
                        )}
                      </TableSortLabel>
                    ) : (
                      column.label
                    )}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <Row
                  key={row.id}
                  row={row}
                  meEmail={me.data?.user?.email}
                  onOpen={(href) => navigate(href)}
                />
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </MaintainerGate>
  );
}
