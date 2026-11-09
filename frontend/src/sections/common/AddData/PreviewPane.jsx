import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  IconButton,
  Link,
  MenuItem,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ImportItemPreview, { KindBadge, TurnDots } from "./ImportItemPreview";
import { PREVIEW_SAMPLE, isIncluded, summaryParts } from "./mappingModel";

const SESSIONS_DOC = "https://docs.futureagi.com/docs/observe";

const EMPTY_TEXT = {
  sessions:
    "No sessions in this window. Widen the range, or check that your traces set session.id.",
  traces: "No traces in this window. Widen the range.",
  queue: "No completed items in this queue yet.",
};

/** `chat-1052` for a session item; `Trace 7e00…4006` (session in the tooltip) for a trace. */
const ItemLabel = ({ item }) => {
  const traceId = item.source?.kind === "trace" ? item.source?.trace_id : null;
  const text = traceId
    ? `Trace ${traceId.slice(0, 4)}…${traceId.slice(-4)}`
    : item.label ?? item.source?.session_name ?? null;
  if (!text) return null;
  const tip = traceId
    ? `${traceId}${item.source?.session_name ? ` · ${item.source.session_name}` : ""}`
    : text;
  return (
    <CustomTooltip show title={tip} size="small" arrow>
      <Typography
        typography="s2"
        fontWeight={500}
        sx={{ fontFamily: "monospace", minWidth: 0 }}
        noWrap
      >
        {text}
      </Typography>
    </CustomTooltip>
  );
};
ItemLabel.propTypes = { item: PropTypes.object };

const formatDuplicate = (text, targetName) => {
  if (!text) return text;
  const name = String(text)
    .replace(/^Already in\s+/, "")
    .trim();
  return targetName && name === targetName
    ? "Already in this dataset"
    : `Also in "${name}"`;
};

// IT-R5 G5 P4: name where the matches already live, from the dedupe result
// (`Already in <dataset>`), so the sentence never claims the wrong dataset.
export const allDuplicatesSentence = (items, targetName, item = "session") => {
  const n = items.length;
  const noun = n === 1 ? `${item} is` : `${item}s are`;
  const lead = n === 1 ? "The matching" : `All ${n} matching`;
  const names = new Set(
    items.map((item) =>
      String(item.warnings?.find((w) => /^Already in /.test(w)) ?? "")
        .replace(/^Already in\s+/, "")
        .trim(),
    ),
  );
  if (names.size === 1) {
    const [name] = names;
    if (!name || name === targetName)
      return `${lead} ${noun} already in this dataset.`;
    return `${lead} ${noun} already in "${name}".`;
  }
  return `${lead} ${noun} already in other datasets.`;
};

const ItemCard = ({
  item,
  index,
  checked,
  selected,
  onSelect,
  onToggle,
  edits,
  editing,
  onEditing,
  onEditCell,
  editDisabledReason,
  displayKind,
  traceLabel,
  targetName,
  hideDraftWarning = false,
}) => {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const dup = item.status === "duplicate";
  const rawDupText = item.warnings?.find((w) => /^Already in /.test(w));
  // L5 G5 (W0-6): a duplicate can be ticked to add it again; it keeps its
  // production meta and says so.
  const dupText = dup
    ? `${formatDuplicate(rawDupText, targetName) ?? "Already imported"}${
        checked ? " · will be added again" : ""
      }`
    : null;
  // The list's one `N drafts need a quick review` notice replaces the
  // per-card `Checks drafted…` warning (IT-R4 G5 P3).
  const warnings = (item.warnings ?? []).filter(
    (w) =>
      w !== rawDupText &&
      !((hideDraftWarning || dup) && /\bdrafted\b/i.test(w)),
  );
  const edited = edits && Object.keys(edits).length > 0;
  return (
    <Box
      data-testid="add-data-item"
      data-item-id={item.temp_id}
      aria-selected={Boolean(selected)}
      role="button"
      tabIndex={0}
      onClick={() => onSelect?.(item.temp_id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect?.(item.temp_id);
        }
      }}
      sx={{
        p: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: selected ? "primary.main" : "divider",
        bgcolor: selected ? "action.selected" : "background.neutral",
        opacity: !checked ? 0.6 : 1,
        cursor: "pointer",
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
        },
      }}
    >
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="center"
        sx={{ mb: 0.75 }}
      >
        <CustomTooltip
          show
          title={
            dup
              ? checked
                ? `${dupText}. Untick to skip it.`
                : `${dupText}. Tick to add it again.`
              : checked
                ? "Leave out"
                : "Include"
          }
          size="small"
          arrow
        >
          <span>
            <Checkbox
              size="small"
              checked={Boolean(checked)}
              onChange={() => onToggle(item.temp_id)}
              inputProps={{ "aria-label": `Include item ${index + 1}` }}
              sx={{ p: 0.25 }}
            />
          </span>
        </CustomTooltip>
        <Typography typography="s3" color="text.disabled">
          {`#${index + 1}`}
        </Typography>
        <ItemLabel item={item} />
        <KindBadge kind={displayKind ?? item.kind} />
        <Box sx={{ flex: 1 }} />
        {dup && (
          <CustomTooltip show title={dupText} size="small" arrow>
            <Chip
              size="small"
              label={checked ? "Adding again" : "Duplicate"}
              data-testid="add-data-dup-chip"
              sx={{
                height: 18,
                typography: "s3",
                bgcolor: "action.hover",
                color: "text.secondary",
                fontWeight: 500,
                "& .MuiChip-label": { px: 0.75 },
              }}
            />
          </CustomTooltip>
        )}
        {warnings.map((w) => (
          <CustomTooltip key={w} show title={w} size="small" arrow>
            <Box
              component="span"
              tabIndex={0}
              aria-label={w}
              sx={{
                display: "inline-flex",
                color: dark ? "warning.light" : "warning.dark",
              }}
            >
              <Iconify icon="mdi:alert-outline" width={16} />
            </Box>
          </CustomTooltip>
        ))}
        {edited && (
          <Chip
            size="small"
            label="Edited"
            sx={{
              height: 18,
              typography: "s3",
              bgcolor: alpha(theme.palette.warning.main, 0.12),
              color: dark ? "warning.light" : "warning.dark",
              "& .MuiChip-label": { px: 0.75 },
            }}
          />
        )}
        {!dup && (
          <CustomTooltip
            show
            title={
              editDisabledReason ??
              (editing ? "Done" : "Edit this item (preview only)")
            }
            size="small"
            arrow
          >
            <span>
              <IconButton
                size="small"
                aria-label={editing ? "Done editing" : `Edit item ${index + 1}`}
                disabled={Boolean(editDisabledReason)}
                onClick={() => onEditing(editing ? null : item.temp_id)}
                sx={{ p: 0.25 }}
              >
                <Iconify
                  icon={editing ? "mdi:check" : "mdi:pencil-outline"}
                  width={16}
                />
              </IconButton>
            </span>
          </CustomTooltip>
        )}
      </Stack>
      {dup && dupText && (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          {`${dupText} · skipped`}
        </Typography>
      )}
      {!dup && (
        <ImportItemPreview
          item={item}
          displayKind={displayKind}
          traceLabel={traceLabel}
          edits={edits}
          editing={editing}
          onEditCell={(name, value) => onEditCell(item.temp_id, name, value)}
        />
      )}
    </Box>
  );
};
ItemCard.propTypes = {
  item: PropTypes.object.isRequired,
  index: PropTypes.number,
  checked: PropTypes.bool,
  selected: PropTypes.bool,
  onSelect: PropTypes.func,
  onToggle: PropTypes.func,
  edits: PropTypes.object,
  editing: PropTypes.bool,
  onEditing: PropTypes.func,
  onEditCell: PropTypes.func,
  editDisabledReason: PropTypes.string,
  displayKind: PropTypes.string,
  traceLabel: PropTypes.string,
  targetName: PropTypes.string,
  hideDraftWarning: PropTypes.bool,
};

const EMPTY_SET = new Set();

const FILTERS = {
  add: (i, unchecked, addAgain) => isIncluded(i, unchecked, addAgain),
  skip: (i, unchecked, addAgain) =>
    i.status === "duplicate" && !addAgain?.has(i.temp_id),
  review: (i, unchecked) =>
    i.status === "new" && i.needs_review && !unchecked.has(i.temp_id),
  rejected: (i) => i.status === "invalid",
};

/**
 * Preview and checks (R2-SPEC §7.3.1, right pane, 420 px): the summary
 * line (each number shows its items), up to 6 items as they will look,
 * each with a checkbox, warnings and inline edit; then `footer` (expected
 * output, version, split).
 */
const PreviewPane = ({
  items,
  counts,
  loading,
  firstLoad,
  error,
  sourceKind,
  unchecked,
  addAgain = EMPTY_SET,
  capped = null,
  paged = false,
  onToggle,
  edits,
  editingId,
  onEditing,
  onEditCell,
  editDisabledReason,
  displayKind,
  traceLabels,
  filter,
  onFilter,
  footer,
  selectedId,
  onSelect,
  targetName,
  hideDuplicates = false,
  allMatchingDuplicates = false,
  itemNoun = "session",
  onRemoveResolutionFilter,
  widenedMessage,
  onReviewDrafts,
  onShowDuplicates,
}) => {
  const visibleItems = hideDuplicates
    ? items.filter((item) => item.status !== "duplicate")
    : items;
  const list = filter
    ? visibleItems.filter((i) => FILTERS[filter]?.(i, unchecked, addAgain))
    : visibleItems.slice(0, PREVIEW_SAMPLE);
  const listedDrafts = items.filter(
    (item) => item.status === "new" && item.needs_review,
  ).length;
  // W3-5: a paged preview (above 1,000 items) lists the first page; the
  // notice counts every draft and says which ones are listed.
  const draftCount =
    paged && counts?.needsReview != null ? counts.needsReview : listedDrafts;
  const parts = counts ? summaryParts(counts) : [];
  const empty = !firstLoad && !error && items.length === 0;
  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Stack spacing={0.5} sx={{ px: 2, pt: 1.5, pb: 1 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            columnGap: 0.5,
            rowGap: 0.25,
          }}
        >
          {parts.map((p, i) => (
            <Box
              key={p.key}
              sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
            >
              {i > 0 && (
                <Typography typography="s2" color="text.disabled">
                  ·
                </Typography>
              )}
              <CustomTooltip
                show
                title={
                  filter === p.key ? "Show the sample" : "Show these items"
                }
                size="small"
                arrow
              >
                <Button
                  size="small"
                  variant="text"
                  onClick={() => onFilter(filter === p.key ? null : p.key)}
                  data-testid={`add-data-summary-${p.key}`}
                  sx={{
                    minWidth: 0,
                    px: 0.25,
                    py: 0,
                    typography: "s2",
                    fontWeight: i === 0 ? 600 : 400,
                    color: filter === p.key ? "primary.main" : "text.primary",
                    textDecoration: filter === p.key ? "underline" : "none",
                  }}
                >
                  {p.text}
                </Button>
              </CustomTooltip>
            </Box>
          ))}
          {loading && !firstLoad && (
            <CircularProgress size={12} thickness={6} sx={{ ml: 0.5 }} />
          )}
        </Box>
        {capped ? (
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="add-data-preview-capped"
          >
            {capped}
          </Typography>
        ) : null}
        {/* IT-R1 G5 P4: what the per-turn dots on the cards mean. */}
        {sourceKind === "sessions" && items.length ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            data-testid="add-data-dot-legend"
          >
            {[
              ["pass", "turn passed"],
              ["fail", "turn failed"],
              ["none", "not scored"],
            ].map(([d, label]) => (
              <Stack key={d} direction="row" alignItems="center" gap={0.5}>
                <TurnDots dots={[d]} />
                <Typography typography="s3" color="text.secondary">
                  {label}
                </Typography>
              </Stack>
            ))}
          </Stack>
        ) : null}
        {allMatchingDuplicates ? (
          <Stack
            spacing={0.75}
            data-testid="add-data-all-duplicates-notice"
            sx={{
              p: 1,
              borderRadius: 0.75,
              bgcolor: "action.hover",
            }}
          >
            <Typography typography="s3" color="text.secondary">
              {allDuplicatesSentence(items, targetName, itemNoun)}
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={0.5}>
              {onShowDuplicates && (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={onShowDuplicates}
                  sx={{ typography: "s3", px: 0.75 }}
                >
                  {`Show the ${items.length} already imported`}
                </Button>
              )}
              {onRemoveResolutionFilter && (
                <Button
                  size="small"
                  variant="text"
                  onClick={onRemoveResolutionFilter}
                  sx={{ typography: "s3", px: 0.5 }}
                >
                  Remove Resolution filter
                </Button>
              )}
            </Stack>
          </Stack>
        ) : widenedMessage ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            data-testid="add-data-widened-notice"
          >
            <Typography typography="s3" color="text.secondary" sx={{ flex: 1 }}>
              {widenedMessage}
            </Typography>
            {onShowDuplicates && (
              <Button
                size="small"
                variant="text"
                onClick={onShowDuplicates}
                sx={{ px: 0.5, minWidth: 0, typography: "s3", fontWeight: 600 }}
              >
                Show them
              </Button>
            )}
          </Stack>
        ) : null}
        {draftCount > 0 && (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            data-testid="add-data-draft-notice"
            sx={{
              px: 1,
              py: 0.75,
              borderRadius: 0.75,
              bgcolor: (t) => alpha(t.palette.warning.main, 0.1),
            }}
          >
            <Typography typography="s3" sx={{ flex: 1 }}>
              {`${draftCount.toLocaleString("en-US")} ${draftCount === 1 ? "draft needs" : "drafts need"} a quick review: expected replies are empty and checks are drafted.${paged && listedDrafts < draftCount ? ` The first ${listedDrafts.toLocaleString("en-US")} are listed here.` : ""}`}
            </Typography>
            <Button
              size="small"
              variant="text"
              onClick={onReviewDrafts}
              sx={{ px: 0.5, minWidth: 0, typography: "s3", fontWeight: 600 }}
            >
              Review drafted checks
            </Button>
          </Stack>
        )}
        {filter && (
          <Typography typography="s3" color="text.secondary">
            {`Showing ${list.length.toLocaleString("en-US")} · `}
            <Link
              component="button"
              type="button"
              onClick={() => onFilter(null)}
              typography="s3"
            >
              Show the sample
            </Link>
          </Typography>
        )}
      </Stack>
      <Stack
        spacing={1.25}
        sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 2, pb: 2 }}
      >
        {error ? (
          <Typography typography="s2" color="error.main" role="alert">
            {error}
          </Typography>
        ) : null}
        {firstLoad && (
          <Stack spacing={1}>
            {[0, 1, 2].map((k) => (
              <Skeleton key={k} variant="rounded" height={92} />
            ))}
          </Stack>
        )}
        {empty && (
          <Stack
            spacing={0.5}
            sx={{ py: 3, alignItems: "center", textAlign: "center" }}
          >
            <Iconify
              icon="mdi:tray-remove"
              width={28}
              sx={{ color: "text.disabled" }}
            />
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ maxWidth: 320 }}
            >
              {EMPTY_TEXT[sourceKind] ?? "Nothing to import."}
            </Typography>
            {sourceKind === "sessions" && (
              <Link
                href={SESSIONS_DOC}
                target="_blank"
                rel="noopener noreferrer"
                typography="s2"
              >
                How to set it ↗
              </Link>
            )}
          </Stack>
        )}
        {!firstLoad && list.length === 0 && filter && (
          <Typography typography="s2" color="text.disabled">
            No items here.
          </Typography>
        )}
        <Stack
          spacing={1}
          onKeyDown={(event) => {
            // Arrow keys move the selection between cards.
            if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
            if (event.target.closest?.("input, textarea, [role=combobox]"))
              return;
            const at = list.findIndex((i) => i.temp_id === selectedId);
            const next =
              list[
                Math.min(
                  list.length - 1,
                  Math.max(0, at + (event.key === "ArrowDown" ? 1 : -1)),
                )
              ];
            if (!next) return;
            event.preventDefault();
            onSelect?.(next.temp_id);
            event.currentTarget
              .querySelector(`[data-item-id="${next.temp_id}"]`)
              ?.focus();
          }}
        >
          {list.map((item) => (
            <ItemCard
              key={item.temp_id}
              item={item}
              index={items.indexOf(item)}
              selected={selectedId === item.temp_id}
              onSelect={onSelect}
              checked={isIncluded(item, unchecked, addAgain)}
              onToggle={onToggle}
              edits={edits[item.temp_id]}
              editing={editingId === item.temp_id}
              onEditing={onEditing}
              onEditCell={onEditCell}
              editDisabledReason={editDisabledReason}
              displayKind={displayKind}
              traceLabel={traceLabels?.[item.source?.trace_id]}
              targetName={targetName}
              hideDraftWarning={draftCount > 0}
            />
          ))}
        </Stack>
        {!filter && items.length > PREVIEW_SAMPLE && (
          <Typography typography="s3" color="text.disabled">
            {`Showing ${PREVIEW_SAMPLE} of ${items.length.toLocaleString("en-US")}`}
          </Typography>
        )}
      </Stack>
      {footer ? (
        <Box
          sx={{
            px: 2,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {footer}
        </Box>
      ) : null}
    </Stack>
  );
};

PreviewPane.propTypes = {
  items: PropTypes.array.isRequired,
  counts: PropTypes.object,
  loading: PropTypes.bool,
  firstLoad: PropTypes.bool,
  error: PropTypes.string,
  sourceKind: PropTypes.string,
  unchecked: PropTypes.instanceOf(Set).isRequired,
  addAgain: PropTypes.instanceOf(Set),
  capped: PropTypes.string,
  paged: PropTypes.bool,
  onToggle: PropTypes.func,
  edits: PropTypes.object,
  editingId: PropTypes.string,
  onEditing: PropTypes.func,
  onEditCell: PropTypes.func,
  editDisabledReason: PropTypes.string,
  displayKind: PropTypes.string,
  traceLabels: PropTypes.object,
  filter: PropTypes.string,
  onFilter: PropTypes.func,
  footer: PropTypes.node,
  selectedId: PropTypes.string,
  onSelect: PropTypes.func,
  targetName: PropTypes.string,
  hideDuplicates: PropTypes.bool,
  allMatchingDuplicates: PropTypes.bool,
  itemNoun: PropTypes.string,
  onRemoveResolutionFilter: PropTypes.func,
  widenedMessage: PropTypes.string,
  onReviewDrafts: PropTypes.func,
  onShowDuplicates: PropTypes.func,
};

/** `Version: pending changes → Save as v2` · split `none ▾`. */
export const TargetVersionLine = ({ text, splits, split, onSplit }) => (
  <Stack direction="row" spacing={1} alignItems="center">
    <Typography
      typography="s2"
      color="text.secondary"
      noWrap
      sx={{ flex: 1, minWidth: 0 }}
    >
      {text}
    </Typography>
    {splits ? (
      <TextField
        select
        size="small"
        label="Split"
        value={split ?? ""}
        onChange={(e) => onSplit(e.target.value || null)}
        SelectProps={{ displayEmpty: true }}
        InputLabelProps={{ shrink: true }}
        sx={{
          width: 120,
          "& .MuiSelect-select": { typography: "s2", py: 0.75 },
        }}
      >
        <MenuItem value="" sx={{ typography: "s2" }}>
          none
        </MenuItem>
        {splits.map((s) => (
          <MenuItem key={s.id} value={s.id} sx={{ typography: "s2" }}>
            {s.name}
          </MenuItem>
        ))}
      </TextField>
    ) : null}
  </Stack>
);
TargetVersionLine.propTypes = {
  text: PropTypes.string,
  splits: PropTypes.array,
  split: PropTypes.string,
  onSplit: PropTypes.func,
};

export default PreviewPane;
