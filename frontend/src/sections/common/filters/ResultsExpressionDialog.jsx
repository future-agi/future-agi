import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { useRunResults } from "src/api/develop/run-results";
import CustomColumnDialog from "src/sections/common/CustomColumns/CustomColumnDialog";
import useCustomColumns from "src/sections/common/CustomColumns/useCustomColumns";
import ExpressionFilterDialog from "./ExpressionFilterDialog";
import { expressionPill } from "./expressionPill";
import { resultsIdsParams } from "./apiCallModel";
import {
  candidateView,
  resultsCatalogForView,
  resultsExamples,
  resultsPlaceholder,
  resultsQuickPicks,
  valueHints,
} from "./resultsDoorModel";

/**
 * the interface contract · the results door (Filter ▾ › Expression…, and a click on an
 * `Expression: …` pill). The live count is one R3 `ids_only=true` call with
 * the page's pills plus the candidate. L7c g1 (the capability contract a): no Copy as API
 * call: the R3 results route is served only by the prototype.
 *
 * Props: open, onClose, onApply(pill), datasetId, view (the FilterMenu view;
 *   `resultsView` = the page's R3 view {runIds, baselineId, pills, show,
 *   search, sort, agg} when the page passes it), editing (the pill being
 *   edited, left out of the count), onSaveAsColumn(expr).
 */
const ResultsExpressionDialog = ({
  open,
  onClose,
  onApply,
  datasetId,
  view,
  editing,
  onSaveAsColumn,
}) => {
  const resultsView = useMemo(() => {
    const runIds = (view?.runs ?? [])
      .filter((r) => !r.not_found)
      .map((r) => r.run_id);
    return (
      view?.resultsView ?? { runIds, baselineId: view?.baselineId, pills: [] }
    );
  }, [view]);
  const otherPills = useMemo(
    () =>
      (resultsView.pills ?? []).filter(
        (p) => !(editing && p.type === "expression" && p.expr === editing.expr),
      ),
    [resultsView, editing],
  );
  // The rows in view (the grid's own query; a cache hit when the page passes its view).
  const { data } = useRunResults(datasetId, resultsView, {
    enabled: !!open && !!datasetId,
  });
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const total = data?.summary?.examples_total ?? null;
  // the interface contract: the dataset's custom columns (`col.<name>`) and the door to add one.
  const custom = useCustomColumns(datasetId, { enabled: !!open });
  const [columnDraft, setColumnDraft] = useState(null);
  const runLabels = useMemo(
    () =>
      Object.fromEntries(
        (view?.runs ?? []).map((r) => [r.run_id ?? r.id, r.label]),
      ),
    [view],
  );
  const outputs = useMemo(
    () =>
      rows
        .flatMap((r) =>
          Object.values(r.results ?? {}).map((x) => x?.output ?? null),
        )
        .filter((o) => o !== null && o !== ""),
    [rows],
  );

  const catalog = useMemo(
    () => resultsCatalogForView(view, rows, custom.columns),
    [view, rows, custom.columns],
  );
  const hints = useMemo(() => valueHints(view, rows), [view, rows]);
  const examples = useMemo(() => resultsExamples(view, rows), [view, rows]);
  const quickPicks = useMemo(() => resultsQuickPicks(view), [view]);

  const count = useCallback(
    async (expr) => {
      try {
        const res = await axios.get(
          endpoints.develop.datasetRuns.results(datasetId),
          {
            params: resultsIdsParams(
              candidateView(resultsView, otherPills, expr),
            ),
          },
        );
        const result = res.data?.result ?? {};
        return {
          matched: result.total ?? (result.example_ids ?? []).length,
          total: total ?? result.total,
          ids: result.example_ids ?? [],
        };
      } catch (e) {
        return {
          error:
            typeof e?.result === "string"
              ? e.result
              : e?.message ?? "Could not count the rows.",
        };
      }
    },
    [datasetId, resultsView, otherPills, total],
  );
  const saveAsColumn =
    onSaveAsColumn ??
    (custom.available
      ? (expr) => setColumnDraft({ kind: "expression", expr })
      : undefined);

  return (
    <>
      <ExpressionFilterDialog
        open={open}
        onClose={onClose}
        onApply={(expr) => onApply(expressionPill(expr))}
        initialExpr={editing?.expr ?? ""}
        catalog={catalog}
        hints={hints}
        examples={examples}
        placeholder={resultsPlaceholder(examples)}
        quickPicks={quickPicks}
        count={count}
        onSaveAsColumn={saveAsColumn}
      />
      {custom.available && (
        <CustomColumnDialog
          open={!!columnDraft}
          onClose={() => setColumnDraft(null)}
          datasetId={datasetId}
          view={resultsView}
          runLabels={runLabels}
          outputs={outputs}
          initial={columnDraft}
        />
      )}
    </>
  );
};

ResultsExpressionDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
  view: PropTypes.object,
  editing: PropTypes.object,
  onSaveAsColumn: PropTypes.func,
};

export default ResultsExpressionDialog;
