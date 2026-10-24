import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableRow,
  TableSortLabel,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { UNITS } from "src/sections/projects/observePopulation";
import { formatPts } from "./insightsChanges";
import {
  availableColumns,
  readColumnPrefs,
  toggleColumn,
  visibleColumns,
  writeColumnPrefs,
} from "./insightsColumns";
import {
  categoryRowId,
  isMutedCategory,
  sortCategoryTree,
} from "./insightsDistribution";
import {
  formatCost,
  formatCount,
  formatLatency,
  formatPercent,
  formatSentiment,
} from "./insightsFormat";
import { sortNotable } from "./insightsNotable";
import { useReport } from "./reportContext";
import { NotableChip, Swatch } from "./parts";

const MOSTLY_ONE_USER = 0.5;
const NOTABLE_IN_ROW = 2;
const CATEGORY_MIN_WIDTH = 260;
const SORTABLE = new Set([
  "share",
  "count",
  "users",
  "sessions",
  "failure",
  "unresolved",
  "sentiment",
  "latency",
  "cost",
  "errors",
]);

function ShareCell({ stats, color }) {
  const share = stats?.share || 0;
  const ci = stats?.share_ci;
  const bar = (
    <Stack direction="row" spacing={1} alignItems="center">
      <Box
        sx={{
          position: "relative",
          width: 52,
          height: 6,
          borderRadius: 3,
          bgcolor: "action.hover",
          flexShrink: 0,
        }}
      >
        {ci && (
          <Box
            aria-hidden
            sx={{
              position: "absolute",
              left: `${ci[0] * 100}%`,
              width: `${Math.max(0, ci[1] - ci[0]) * 100}%`,
              top: -2,
              bottom: -2,
              borderRadius: 1,
              bgcolor: alpha(color || "#999", 0.18),
            }}
          />
        )}
        <Box
          sx={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: `${share * 100}%`,
            borderRadius: 3,
            bgcolor: color || "grey.500",
          }}
        />
      </Box>
      <Typography
        variant="body2"
        sx={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}
      >
        {formatPercent(share)}
        {Number.isFinite(stats?.count) && (
          <Box
            component="span"
            sx={{ ml: 0.75, color: "text.secondary", fontSize: 12 }}
          >
            {formatCount(stats.count)}
          </Box>
        )}
      </Typography>
    </Stack>
  );
  return ci ? (
    <Tooltip
      describeChild
      arrow
      title={`95% CI ${formatPercent(ci[0])}–${formatPercent(ci[1])}: with this sample size, the true share is very likely in this range.`}
    >
      {bar}
    </Tooltip>
  ) : (
    bar
  );
}

ShareCell.propTypes = { stats: PropTypes.object, color: PropTypes.string };

function ChangeCell({ change }) {
  const theme = useTheme();
  if (!change) return <Muted>–</Muted>;
  if (change.isNew)
    return (
      <Chip
        size="small"
        label="New"
        sx={{
          height: 20,
          fontSize: 11,
          borderRadius: 0.75,
          color: "info.dark",
          bgcolor: "info.lighter",
        }}
      />
    );
  if (!Number.isFinite(change.delta)) return <Muted>–</Muted>;
  const strong = change.significant && Math.abs(change.delta) >= 0.005;
  const icon =
    change.delta > 0
      ? "mdi:arrow-up"
      : change.delta < 0
        ? "mdi:arrow-down"
        : "mdi:minus";
  return (
    <Tooltip
      arrow
      describeChild
      title={
        strong
          ? `Was ${formatPercent(change.previousShare)} last run. A real change: it holds after correcting for every comparison.`
          : `Was ${formatPercent(change.previousShare)} last run. Within the noise for this sample size.`
      }
    >
      <Stack
        direction="row"
        spacing={0.25}
        alignItems="center"
        justifyContent="flex-end"
        sx={{
          color: strong ? "text.primary" : "text.secondary",
          fontWeight: strong ? 600 : 400,
        }}
      >
        <Iconify
          icon={icon}
          width={13}
          sx={{
            color: strong
              ? theme.palette.text.primary
              : theme.palette.text.disabled,
          }}
        />
        <span>{formatPts(change.delta)}</span>
      </Stack>
    </Tooltip>
  );
}

ChangeCell.propTypes = { change: PropTypes.object };

function Muted({ children }) {
  return (
    <Typography
      variant="body2"
      component="span"
      color="text.disabled"
      sx={{ fontSize: 13 }}
    >
      {children}
    </Typography>
  );
}

Muted.propTypes = { children: PropTypes.node };

function attributeValue(summary, type) {
  if (!summary) return "–";
  if ((summary.type || type) === "boolean") return formatPercent(summary.rate);
  if ((summary.type || type) === "number")
    return Number.isFinite(summary.mean) ? summary.mean.toFixed(1) : "–";
  return summary.top?.[0]?.value ?? "–";
}

function cellValue(column, category, ctx) {
  const stats = category.stats || {};
  if (column.attribute)
    return attributeValue(
      stats.attributes?.[column.attribute],
      column.attributeType,
    );
  switch (column.key) {
    case "share":
      return <ShareCell stats={stats} color={ctx.color} />;
    case "users": {
      const mostlyOne = (stats.top_user_share || 0) > MOSTLY_ONE_USER;
      return (
        <Stack
          direction="row"
          spacing={0.5}
          alignItems="center"
          justifyContent="flex-end"
        >
          {mostlyOne && (
            <Tooltip
              describeChild
              arrow
              title={`Mostly one user: ${formatPercent(stats.top_user_share)} of this category comes from a single user.`}
            >
              <Iconify
                icon="mdi:account-alert-outline"
                width={15}
                sx={{ color: "warning.dark" }}
                aria-label="Mostly one user"
              />
            </Tooltip>
          )}
          <span>{formatCount(stats.users)}</span>
        </Stack>
      );
    }
    case "notable": {
      const notable = sortNotable(category.notable);
      if (!notable.length) return <Muted>–</Muted>;
      return (
        <Stack spacing={0.5} alignItems="flex-start" sx={{ minWidth: 0 }}>
          {notable.slice(0, NOTABLE_IN_ROW).map((note) => (
            <NotableChip key={`${note.metric}-${note.kind}`} note={note} />
          ))}
          {notable.length > NOTABLE_IN_ROW && (
            <Typography variant="caption" color="text.secondary">
              +{notable.length - NOTABLE_IN_ROW} more
            </Typography>
          )}
        </Stack>
      );
    }
    case "change":
      return <ChangeCell change={ctx.change} />;
    case "count":
      return formatCount(stats.count);
    case "sessions":
      return formatCount(stats.sessions);
    case "failure":
      return formatPercent(stats.failure_rate);
    case "unresolved":
      return formatPercent(stats.unresolved_rate);
    case "sentiment":
      return formatSentiment(stats.sentiment);
    case "latency":
      return formatLatency(stats.latency_ms);
    case "cost":
      return stats.cost > 0 ? formatCost(stats.cost) : "–";
    case "errors":
      return formatPercent(stats.error_rate);
    default:
      return "–";
  }
}

function CategoryRow({
  category,
  child,
  color,
  columns,
  expandable,
  expanded,
  onToggle,
  onOpen,
}) {
  const report = useReport();
  const stats = category.stats || {};
  const muted = isMutedCategory(category);
  const open = () => onOpen(category.id);
  const ctx = { color, change: report.changes.get(category.id) };

  return (
    <TableRow
      id={categoryRowId(category.id)}
      hover
      tabIndex={0}
      onClick={open}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
        if (expandable && event.key === "ArrowRight" && !expanded)
          onToggle(category.id);
        if (expandable && event.key === "ArrowLeft" && expanded)
          onToggle(category.id);
      }}
      aria-label={`${category.name}: ${formatCount(stats.count)} (${formatPercent(stats.share)})`}
      sx={{
        cursor: "pointer",
        bgcolor: child ? "background.neutral" : undefined,
        "& > td": {
          color: muted ? "text.secondary" : undefined,
          fontSize: 13,
          py: 1.25,
        },
        "&:hover .open-hint": { opacity: 1 },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: -2,
        },
      }}
    >
      <TableCell padding="none" sx={{ width: 36, pl: 0.5 }}>
        {expandable && (
          <IconButton
            size="small"
            aria-label={`${expanded ? "Hide" : "Show"} subcategories of ${category.name}`}
            aria-expanded={expanded}
            onClick={(event) => {
              event.stopPropagation();
              onToggle(category.id);
            }}
          >
            <Iconify
              icon={expanded ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={18}
            />
          </IconButton>
        )}
      </TableCell>
      <TableCell sx={{ pl: child ? 3 : 0.5 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Swatch
            color={
              muted ? undefined : child && color ? alpha(color, 0.55) : color
            }
            size={child ? 8 : 10}
          />
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Stack direction="row" spacing={0.75} alignItems="center">
              <Typography
                variant="body2"
                title={category.name}
                sx={{
                  fontSize: 13,
                  fontWeight: child ? 500 : 600,
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}
              >
                {category.name}
              </Typography>
              {category.status === "emerging" && (
                <Tooltip
                  describeChild
                  arrow
                  title="New in this run: these items did not fit the report's saved categories."
                >
                  <Chip
                    size="small"
                    label="New"
                    sx={{
                      height: 18,
                      fontSize: 10,
                      borderRadius: 0.75,
                      color: "info.dark",
                      bgcolor: "info.lighter",
                    }}
                  />
                </Tooltip>
              )}
            </Stack>
            {category.description && (
              <Typography
                variant="caption"
                color="text.secondary"
                noWrap
                component="div"
                title={category.description}
              >
                {category.description}
              </Typography>
            )}
          </Box>
          <Iconify
            className="open-hint"
            icon="mdi:arrow-top-right"
            width={14}
            sx={{ color: "text.disabled", opacity: 0, flexShrink: 0 }}
          />
        </Stack>
      </TableCell>
      {columns.map((column) => (
        <TableCell
          key={column.key}
          align={column.align}
          sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
        >
          {cellValue(column, category, ctx)}
        </TableCell>
      ))}
    </TableRow>
  );
}

CategoryRow.propTypes = {
  category: PropTypes.object.isRequired,
  child: PropTypes.bool,
  color: PropTypes.string,
  columns: PropTypes.array.isRequired,
  expandable: PropTypes.bool,
  expanded: PropTypes.bool,
  onToggle: PropTypes.func,
  onOpen: PropTypes.func.isRequired,
};

function ColumnsMenu({ columns, prefs, onChange }) {
  const [anchor, setAnchor] = useState(null);
  const shown = new Set(visibleColumns(columns, prefs).map((c) => c.key));
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        startIcon={<Iconify icon="mdi:view-column-outline" width={16} />}
        onClick={(event) => setAnchor(event.currentTarget)}
        aria-haspopup="menu"
        sx={{ fontSize: 13, fontWeight: 500 }}
      >
        Columns
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        MenuListProps={{ dense: true, "aria-label": "Columns" }}
        PaperProps={{ sx: { maxHeight: 420, minWidth: 220 } }}
      >
        <MenuItem disabled sx={{ opacity: "1 !important" }}>
          <Checkbox size="small" checked disabled sx={{ p: 0.5, mr: 1 }} />
          <ListItemText primary="Category" />
        </MenuItem>
        {columns.map((column, index) => (
          <MenuItem
            key={column.key}
            role="menuitemcheckbox"
            aria-checked={shown.has(column.key)}
            onClick={() => onChange(toggleColumn(columns, prefs, column.key))}
            divider={
              Boolean(column.attribute) === false &&
              Boolean(columns[index + 1]?.attribute)
            }
          >
            <Checkbox
              size="small"
              checked={shown.has(column.key)}
              tabIndex={-1}
              sx={{ p: 0.5, mr: 1 }}
            />
            <ListItemText
              primary={column.label}
              secondary={column.attribute ? "Attribute" : undefined}
              secondaryTypographyProps={{ variant: "caption" }}
            />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

ColumnsMenu.propTypes = {
  columns: PropTypes.array.isRequired,
  prefs: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};

/**
 * The categories of a run: one row per category with its share, users and
 * what sets it apart; subcategories expand inline and a row opens the
 * category explorer. Columns are chosen from a menu and kept per report.
 */
export default function CategoryTable({ reportId, run, attributes }) {
  const report = useReport();
  const unit = run.unit;
  const totals = run.result?.totals || {};
  const [expanded, setExpanded] = useState(() => new Set());
  const [sort, setSort] = useState({ key: "share", direction: "desc" });
  const [prefs, setPrefs] = useState(() => readColumnPrefs(reportId));
  const columns = useMemo(
    () =>
      availableColumns({
        unit,
        attributes,
        hasChanges: Boolean(run.result?.changes),
      }),
    [unit, attributes, run.result?.changes],
  );
  const shown = visibleColumns(columns, prefs);
  const rows = useMemo(
    () => sortCategoryTree(report.tree, sort.key, sort.direction),
    [report.tree, sort],
  );
  const expandable = report.tree
    .filter((row) => row.expandable)
    .map((row) => row.id);
  const allOpen =
    expandable.length > 0 && expandable.every((id) => expanded.has(id));
  const plural = (UNITS[unit] || UNITS.traces).plural;

  const toggle = (id) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const sortBy = (key) =>
    setSort((current) => ({
      key,
      direction:
        current.key === key && current.direction === "desc" ? "asc" : "desc",
    }));
  const changePrefs = (next) => {
    setPrefs(next);
    writeColumnPrefs(reportId, next);
  };

  const totalsRow = {
    id: "__all__",
    name: `All ${plural} analysed`,
    stats: {
      ...totals,
      share: 1,
    },
    notable: [],
  };

  return (
    <Box component="section" aria-labelledby="insights-categories-title">
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ mb: 1.25 }}
      >
        <Typography
          id="insights-categories-title"
          variant="subtitle1"
          component="h2"
          sx={{ fontSize: 15 }}
        >
          Categories
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
          Click a category to explore its {plural}
        </Typography>
        {expandable.length > 0 && (
          <Button
            size="small"
            color="inherit"
            onClick={() =>
              setExpanded(allOpen ? new Set() : new Set(expandable))
            }
            sx={{ fontSize: 13, fontWeight: 500, color: "text.secondary" }}
          >
            {allOpen ? "Collapse all" : "Expand all"}
          </Button>
        )}
        <ColumnsMenu columns={columns} prefs={prefs} onChange={changePrefs} />
      </Stack>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflowX: "auto",
        }}
      >
        <Table
          size="small"
          id="insights-categories"
          sx={{
            tableLayout: "fixed",
            minWidth:
              CATEGORY_MIN_WIDTH +
              36 +
              shown.reduce((sum, column) => sum + (column.width || 96), 0),
            "& td, & th": { borderColor: "divider" },
            "& thead th": {
              bgcolor: "background.neutral",
              color: "text.secondary",
              fontSize: 12,
              fontWeight: 500,
              py: 0.75,
              whiteSpace: "nowrap",
            },
          }}
        >
          <TableHead>
            <TableRow>
              <TableCell padding="none" sx={{ width: 36 }} />
              <TableCell sx={{ pl: 0.5 }}>Category</TableCell>
              {shown.map((column) => {
                const sortable = SORTABLE.has(column.key);
                const label = sortable ? (
                  <TableSortLabel
                    active={sort.key === column.key}
                    direction={
                      sort.key === column.key ? sort.direction : "desc"
                    }
                    onClick={() => sortBy(column.key)}
                  >
                    {column.label}
                  </TableSortLabel>
                ) : (
                  column.label
                );
                return (
                  <TableCell
                    key={column.key}
                    align={column.align}
                    sx={{ width: column.width }}
                    sortDirection={
                      sort.key === column.key ? sort.direction : false
                    }
                  >
                    {column.help ? (
                      <Tooltip describeChild arrow title={column.help}>
                        <span>{label}</span>
                      </Tooltip>
                    ) : (
                      label
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => {
              const isOpen = expanded.has(row.id);
              const color = report.colorOf(row.id);
              return (
                <React.Fragment key={row.id}>
                  <CategoryRow
                    category={row}
                    color={color}
                    columns={shown}
                    expandable={row.expandable}
                    expanded={isOpen}
                    onToggle={toggle}
                    onOpen={report.openCategory}
                  />
                  {row.expandable &&
                    isOpen &&
                    row.childRows.map((child) => (
                      <CategoryRow
                        key={child.id}
                        category={child}
                        child
                        color={color}
                        columns={shown}
                        onOpen={report.openCategory}
                      />
                    ))}
                </React.Fragment>
              );
            })}
          </TableBody>
          <TableFooter>
            <TableRow
              sx={{
                "& td": {
                  borderBottom: 0,
                  color: "text.secondary",
                  fontSize: 12,
                  fontVariantNumeric: "tabular-nums",
                  whiteSpace: "nowrap",
                  py: 1,
                },
              }}
            >
              <TableCell padding="none" />
              <TableCell sx={{ pl: 0.5 }}>
                {totalsRow.name}: {formatCount(totals.count)}
              </TableCell>
              {shown.map((column) => (
                <TableCell key={column.key} align={column.align}>
                  {["share", "notable", "change"].includes(column.key)
                    ? ""
                    : cellValue(column, totalsRow, {})}
                </TableCell>
              ))}
            </TableRow>
          </TableFooter>
        </Table>
      </Box>
    </Box>
  );
}

CategoryTable.propTypes = {
  reportId: PropTypes.string,
  run: PropTypes.object.isRequired,
  attributes: PropTypes.array,
};
