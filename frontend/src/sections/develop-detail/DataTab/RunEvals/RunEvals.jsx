import React, { useMemo, useState } from "react";
import { DialogContent, Skeleton, Stack, useTheme } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import { AgGridReact } from "ag-grid-react";
import PropTypes from "prop-types";
import { useAgThemeWithoutGridWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";

const RunEvals = ({ gridRef, setIsData }) => {
  const [mainData, setMainData] = useState(null);
  const { dataset } = useParams();
  const theme = useTheme();
  const agThemeWithoutGrid = useAgThemeWithoutGridWith(
    AG_THEME_OVERRIDES.withColumnBorder,
  );

  const { data: datasetUserEvalList, isLoading: isLoadingUserEvalList } =
    useQuery({
      queryFn: () =>
        axios.get(endpoints.develop.eval.getEvalsList(dataset), {
          params: {
            eval_type: "user",
          },
        }),
      queryKey: ["develop", "user-eval-list", dataset],
      select: (d) => d?.data?.result?.evals,
    });

  const { mutate: runPrompts, isPending: runPromptLoading } = useMutation({
    mutationFn: async () => {
      const response = await axios.get(
        endpoints.develop.getDatasetDetail(dataset),
        {},
      );
      return response.data;
    },
    onSuccess: (data) => {
      const runPromptData = data?.result?.column_config?.filter(
        (item) => item?.origin_type === "run_prompt",
      );
      setMainData(runPromptData);
    },
  });

  const loading = isLoadingUserEvalList || runPromptLoading;

  const rowSelection = useMemo(() => {
    return {
      mode: "multiRow",
      checkboxes: true,
      headerCheckbox: false,
      enableSelectionWithoutKeys: true,
      enableClickSelection: true,
    };
  }, []);

  const rowData = useMemo(() => {
    if (!mainData) {
      runPrompts();
    }

    if (datasetUserEvalList && mainData) {
      const mergedData = [...datasetUserEvalList, ...mainData];

      return mergedData.map((item) => ({
        content: item?.name || item?.eval_template_name,
        field: item?.origin_type === "run_prompt" ? item.source_id : item.id,
        originType:
          item?.origin_type === "run_prompt" ? item.origin_type : "eval",
      }));
    }
    return [];
  }, [datasetUserEvalList, mainData]);

  const columnDefs = [
    // {
    //   headerCheckboxSelection: true,
    //   checkboxSelection: true,
    //   width: 50,

    // },
    {
      field: "content",
      flex: 1,
      cellStyle: { display: "flex", alignItems: "center" },
    },
  ];

  const onSelectionChanged = () => {
    const selectedRows = gridRef?.current?.api?.getSelectedRows();
    setIsData(selectedRows.length > 0);
  };

  return (
    <DialogContent sx={{ padding: 0, margin: 0 }}>
      {loading ? (
        <Stack
          sx={{ my: theme.spacing(0.8) }}
          direction={"column"}
          gap={theme.spacing(1)}
        >
          {Array(10)
            .fill(0)
            .map((_, index) => (
              <Skeleton
                key={index}
                variant="text"
                height={30}
                width={100 * (Math.random() + 3)}
              />
            ))}
        </Stack>
      ) : (
        <div className="ag-theme-alpine">
          <AgGridReact
            ref={gridRef}
            theme={agThemeWithoutGrid}
            gridOptions={{
              suppressCellSelection: true,
              cellFocused: false,
            }}
            columnDefs={columnDefs}
            rowData={rowData}
            domLayout="autoHeight"
            rowSelection={rowSelection}
            rowHeight={35}
            headerHeight={0}
            overlayNoRowsTemplate={
              '<span style="padding: 10px; color: var(--text-muted);">No data available</span>'
            }
            onSelectionChanged={onSelectionChanged}
          />
        </div>
      )}
    </DialogContent>
  );
};

RunEvals.propTypes = {
  setIsData: PropTypes.func,
  gridRef: PropTypes.any,
};

export default RunEvals;
