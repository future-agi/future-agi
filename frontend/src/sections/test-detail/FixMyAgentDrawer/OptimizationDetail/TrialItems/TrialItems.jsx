import { Box, Typography } from "@mui/material";
import { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import { useAgTheme } from "src/hooks/use-ag-theme";
import SvgColor from "../../../../../components/svg-color";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "../../../../../utils/axios";
import PropTypes from "prop-types";
import EvalCellRenderer from "../../../CellRenderers/EvalCellRenderer";

import logger from "../../../../../utils/logger";
import PromptTooltip from "../../OptimizationResults/CellRenderers/PromptTooltip";

const getTrialItemsColumnConfig = (columnConfig) => {
  if (!columnConfig) return [];
  return columnConfig.map((column) => {
    const isEvaluationColumn =
      column.id !== "id" &&
      column.id !== "input_text" &&
      column.id !== "output_text";

    if (isEvaluationColumn) {
      return {
        field: column.id,
        headerName: column.name,
        minWidth: 150,
        cellRenderer: EvalCellRenderer,
        valueGetter: (params) => {
          const value = params.data?.[column.id];
          if (value == null) {
            return { type: "score", value: null };
          }
          return {
            type: "score",
            value: typeof value === "number" ? value : parseFloat(value),
          };
        },
        cellStyle: {
          padding: 0,
        },
      };
    }

    // Text columns
    return {
      field: column.id,
      headerName: column.name,
      wrapText: true,
      flex: 1,
      minWidth: 400,
      tooltipComponent: PromptTooltip,
      tooltipValueGetter: ({ data }) => data?.[column.id],
      cellStyle: {
        lineHeight: 1.5,
      },
    };
  });
};

const TrialItems = ({ optimizationId, trialId }) => {
  const agTheme = useAgTheme();
  const { data: trialItems } = useQuery({
    queryKey: ["fix-my-agent-trial-items", optimizationId, trialId],
    queryFn: () =>
      axios.get(
        endpoints.optimizeSimulate.getTrialItems(optimizationId, trialId),
      ),
    select: (data) => data?.data?.result,
    enabled: !!optimizationId && !!trialId,
  });

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: true,
      filter: false,
      resizable: true,
      suppressMenu: true,
      //   cellStyle: {
      //     lineHeight: 1.5,
      //     padding: "8px",
      //     display: "flex",
      //     alignItems: "center",
      //   },
    }),
    [],
  );

  const columnDefs = useMemo(() => {
    return getTrialItemsColumnConfig(trialItems?.column_config);
  }, [trialItems?.column_config]);

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 2, height: "100%" }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          backgroundColor: "blue.o5",
          paddingX: "12px",
          paddingY: "4px",
          borderRadius: "4px",
        }}
      >
        <SvgColor
          src="/assets/icons/ic_info.svg"
          sx={{ width: 16, height: 16, color: "blue.500" }}
        />
        <Typography typography="s2">
          Iterations ran to optimize the prompt
        </Typography>
      </Box>
      <Typography typography="s1" fontWeight="fontWeightMedium">
        Trial Items : {trialItems?.table?.length || 0}
      </Typography>
      <Box sx={{ width: "100%", flex: 1, minHeight: 0 }}>
        <AgGridReact
          theme={agTheme.withParams({
            headerColumnBorder: {
              width: "0px",
            },
          })}
          rowHeight={100}
          rowSelection={undefined}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          pagination={true}
          paginationPageSizeSelector={false}
          rowData={trialItems?.table || []}
          getRowId={({ data }) => data.id}
          paginationPageSize={10}
          tooltipShowDelay={200}
          tooltipInteraction={true}
        />
      </Box>
    </Box>
  );
};

TrialItems.propTypes = {
  optimizationId: PropTypes.string,
  trialId: PropTypes.string,
};

export default TrialItems;
