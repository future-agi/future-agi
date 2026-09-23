import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, Stack, Typography, Pagination, CircularProgress, Collapse, Button } from "@mui/material";

import Iconify from "src/components/iconify";
import ScenarioTable from "./ScenarioTable";
import GroupedScenarioList from "./GroupedScenarioList";
import { SCENARIOS_COPY } from "./scenarios.constants";

/**
 * The Scenarios list body — table or list, one page at a time.
 *
 * Everything here is driven by one page of rows (from useScenarioPage, the
 * backend seam) and one predicate selection (useSelection). The header checkbox
 * only ever selects the visible page; the banner is the sole bridge to "all N
 * matching"; the pager is plain numbered buttons. When `showInspector` is set
 * (the ?scnDemo scale harness) a collapsible panel shows the exact list request
 * and the exact bulk payload, so the server-side contract is visible.
 */
export default function PagedScenarioViews({
  pageData, selection, env, view, hiddenGroupIds, onHideGroup, onEdit, onRemove,
  page, onPageChange, query, filters, groupBy, showInspector, locked,
}) {
  const [inspect, setInspect] = useState(false);
  const { rows, total, pageCount, limit, pageGroups, pageIds, loading } = pageData;

  const shownGroups = pageGroups.filter((g) => !hiddenGroupIds.includes(g.id));
  const allHidden = shownGroups.length === 0 && hiddenGroupIds.length > 0 && !query.trim();
  const isEmpty = shownGroups.length === 0;

  // Once there's more than a page, the rows live in a fixed-height scroll box
  // with the pager BELOW it. Fixed height (not max) so the box — and therefore
  // the pager under it — never changes size between pages: the pager stays put
  // under the cursor for repeated Next clicks. A page change resets only THIS
  // box to the top (so page N opens at row 1); it never scrolls the page, so
  // the pager doesn't move. A single page renders naturally (no empty box).
  const bounded = pageCount > 1;
  const scrollRef = useRef(null);
  useEffect(() => {
    if (bounded) scrollRef.current?.scrollTo?.({ top: 0 });
  }, [page, bounded]);
  // This box is the ONE scroll container for the table — both axes — so the
  // sticky column/group headers pin to it. Bounded (multi-page): a fixed height
  // that scrolls vertically. Single page: natural height, horizontal scroll only
  // (the table is wide), taking over the scroll the table no longer wraps itself.
  const scrollSx = bounded
    ? { height: "clamp(300px, calc(100dvh - 380px), 640px)", overflow: "auto" }
    : { overflowX: "auto" };

  // Header checkbox toggles the whole page. When the page is already fully
  // selected via an escalated "all matching", un-checking clears the predicate.
  const onTogglePage = (checked) => {
    if (checked) selection.setPage(pageIds, true);
    else if (selection.mode === "all") selection.clear();
    else selection.setPage(pageIds, false);
  };

  const rangeStart = total === 0 ? 0 : page * limit + 1;
  const rangeEnd = Math.min(total, rangeStart + rows.length - 1);

  const listPayload = { search: query || undefined, filters, groupBy, page, limit };
  const bulkPayload = selection.payload({ search: query || undefined, filters });

  return (
    <Box sx={{ position: "relative" }}>
      {/* The bulk-action bar isn't here — it takes over the toolbar row above
          (in ScenariosStep) while a selection is active, so acting on rows
          never shifts this table down. */}

      {/* Loading veil — a page fetch is a real round trip in the seam. */}
      {loading && (
        <Box sx={{ position: "absolute", inset: 0, zIndex: 3, display: "grid", placeItems: "center", bgcolor: (t) => `${t.palette.background.paper}99` }}>
          <CircularProgress size={22} />
        </Box>
      )}

      <Box ref={scrollRef} sx={scrollSx}>
        {isEmpty ? (
          <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {allHidden ? SCENARIOS_COPY.allHidden : SCENARIOS_COPY.noMatch}
            </Typography>
          </Box>
        ) : view === "table" ? (
          <ScenarioTable
            rows={rows}
            groups={shownGroups}
            env={env}
            onEdit={onEdit}
            onRemove={onRemove}
            onHideGroup={onHideGroup}
            selection={selection}
            pageIds={pageIds}
            onTogglePage={onTogglePage}
            locked={locked}
          />
        ) : (
          <GroupedScenarioList
            groups={shownGroups}
            env={env}
            onEdit={onEdit}
            onRemove={onRemove}
            onHideGroup={onHideGroup}
            selection={selection}
            locked={locked}
          />
        )}
      </Box>

      {/* Pager + range. Numbered buttons: one deterministic request per page.
          A single page needs neither the pager nor the range line. */}
      {pageCount > 1 && (
        <Stack
          direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1}
          sx={{ px: 2, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
            Showing {rangeStart.toLocaleString()}–{rangeEnd.toLocaleString()} of {total.toLocaleString()}
          </Typography>
          <Pagination
            size="small"
            shape="rounded"
            count={pageCount}
            page={page + 1}
            onChange={(_, p) => onPageChange(p - 1)}
            siblingCount={1}
            boundaryCount={1}
          />
        </Stack>
      )}

      {/* Scale-harness inspector (?scnDemo) — the server contract made visible. */}
      {showInspector && (
      <Box sx={{ px: 2, pb: 1.5 }}>
        <Button
          size="small" variant="text" onClick={() => setInspect((v) => !v)}
          startIcon={<Iconify icon={inspect ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"} width={14} />}
          sx={{ typography: "s3", color: "text.subtitle", px: 0.5 }}
        >
          Backend calls (POC)
        </Button>
        <Collapse in={inspect}>
          <Box sx={{ p: 1.5, borderRadius: 1, bgcolor: "background.neutral", border: "1px dashed", borderColor: "divider" }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}>
              GET /scenarios — one request per page
            </Typography>
            <Box component="pre" sx={{ m: 0, mb: 1.5, typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", whiteSpace: "pre-wrap", color: "text.secondary" }}>
              {JSON.stringify(listPayload, null, 2)}
            </Box>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}>
              POST /scenarios/bulk-delete — {selection.mode === "all" ? "by predicate + exclusions" : "by id list"}
            </Typography>
            <Box component="pre" sx={{ m: 0, typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", whiteSpace: "pre-wrap", color: "text.secondary" }}>
              {JSON.stringify(bulkPayload, null, 2)}
            </Box>
          </Box>
        </Collapse>
      </Box>
      )}
    </Box>
  );
}

PagedScenarioViews.propTypes = {
  pageData: PropTypes.object.isRequired,
  selection: PropTypes.object.isRequired,
  env: PropTypes.object,
  view: PropTypes.oneOf(["table", "list"]).isRequired,
  hiddenGroupIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  onHideGroup: PropTypes.func.isRequired,
  onEdit: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  page: PropTypes.number.isRequired,
  onPageChange: PropTypes.func.isRequired,
  query: PropTypes.string,
  filters: PropTypes.object,
  groupBy: PropTypes.string,
  showInspector: PropTypes.bool,
  locked: PropTypes.bool,
};
