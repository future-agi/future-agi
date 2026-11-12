import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  getDataGridFilters,
  getDataGridSearch,
  useDataGridView,
} from "src/sections/develop-detail/DataTab/DataGrid/useDataGridView";
import ExpressionFilterDialog from "./ExpressionFilterDialog";
import {
  gridPlaceholder,
  expressionGridFilter,
  gridCatalogForColumns,
  gridColumnsFromConfig,
  gridExamples,
  gridExpressionError,
  gridQuickPicks,
  gridTableParams,
  gridValueHints,
  withoutGridExpression,
} from "./gridDoorModel";

// The rows read for value hints and examples: get-dataset-table's own page cap.
const HINT_PAGE = 500;

const tableGet = (datasetId, params) =>
  axios
    .get(endpoints.develop.getDatasetDetail(datasetId), { params })
    .then((res) => res.data?.result ?? {});

/**
 * the interface contract · `{ } Expression` beside the Data grid's filter chips. It opens
 * `Filter by expression` in grid mode: the catalogue is the dataset's
 * columns, the count is get-dataset-table `total_rows` with the candidate
 * `system:expression` filter added to the grid's own filters, and Apply
 * keeps the expression in the grid view (URL `expr=`). L7c g1 (the capability contract
 * a): no Copy as API call: the real get-dataset-table view does not read the
 * `system:expression` filter, and an as-of view reads a version route the
 * real API does not serve, so the printed call could not return this view.
 */
const ExpressionFilterButton = ({ datasetId, showLabel = true }) => {
  const view = useDataGridView(datasetId);
  const expr = view.expression ?? null;
  const setExpression = view.setExpression;
  const [open, setOpen] = useState(false);

  // Columns and rows behind the hints (the unfiltered table, one read per open).
  const { data } = useQuery({
    queryKey: ["w5-grid-expression-hints", datasetId],
    queryFn: () =>
      tableGet(datasetId, gridTableParams([], "", { pageSize: HINT_PAGE })),
    enabled: open && !!datasetId,
    staleTime: 30_000,
  });
  const columns = useMemo(
    () => gridColumnsFromConfig(data?.column_config),
    [data],
  );
  const rows = useMemo(() => data?.table ?? [], [data]);
  const catalog = useMemo(() => gridCatalogForColumns(columns), [columns]);
  const hints = useMemo(() => gridValueHints(columns, rows), [columns, rows]);
  const examples = useMemo(() => gridExamples(columns, rows), [columns, rows]);
  const quickPicks = useMemo(() => gridQuickPicks(columns), [columns]);

  const count = useCallback(
    async (candidate) => {
      const local = columns.length
        ? gridExpressionError(candidate, catalog)
        : null;
      if (local) return { error: local };
      const base = withoutGridExpression(getDataGridFilters(datasetId));
      const search = getDataGridSearch();
      try {
        const [withExpr, without] = await Promise.all([
          tableGet(
            datasetId,
            gridTableParams([...base, expressionGridFilter(candidate)], search),
          ),
          tableGet(datasetId, gridTableParams(base, search)),
        ]);
        return {
          matched: withExpr?.metadata?.total_rows ?? 0,
          total: without?.metadata?.total_rows ?? 0,
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
    [datasetId, columns, catalog],
  );

  // Only the versioned dataset grid applies system filters (its view state).
  if (!setExpression || !datasetId) return null;
  return (
    <>
      <CustomTooltip
        show
        title={expr ?? "Filter rows by an expression"}
        placement="top"
        size="small"
        arrow
      >
        <Button
          size="small"
          color="inherit"
          aria-label={expr ? "Expression filter, active" : "Expression filter"}
          aria-pressed={!!expr}
          startIcon={<Iconify icon="mdi:code-parentheses" width={18} />}
          onClick={() => setOpen(true)}
          data-testid="grid-expression-button"
          sx={{
            typography: "s2",
            color: expr ? "primary.main" : "text.secondary",
            minWidth: 0,
            px: showLabel ? 1 : 0.75,
            whiteSpace: "nowrap",
            flexShrink: 0,
            ...(expr && { bgcolor: "action.selected" }),
            "& .MuiButton-startIcon": showLabel ? undefined : { m: 0 },
          }}
        >
          {showLabel ? "Expression" : null}
        </Button>
      </CustomTooltip>
      <ExpressionFilterDialog
        open={open}
        onClose={() => setOpen(false)}
        onApply={(next) => {
          setExpression(next);
          setOpen(false);
        }}
        initialExpr={expr ?? ""}
        catalog={columns.length ? catalog : undefined}
        hints={hints}
        examples={examples}
        quickPicks={quickPicks}
        count={count}
        placeholder={gridPlaceholder(examples)}
        examplesTitle="Examples from this dataset"
      />
    </>
  );
};

ExpressionFilterButton.propTypes = {
  datasetId: PropTypes.string,
  showLabel: PropTypes.bool,
};

export default ExpressionFilterButton;
