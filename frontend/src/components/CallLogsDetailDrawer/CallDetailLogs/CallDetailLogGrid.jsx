import { Box, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { CallDetailLogColumnDefs } from "./common";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import LogDetailDrawer from "./LogDetailDrawer";
import PropTypes from "prop-types";
import {
  useCallLogsSearchStore,
  useCallLogsSearchStoreShallow,
} from "./states";

// Custom overlay component for errors
const CustomErrorOverlay = () => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: 2,
      }}
    >
      <Typography typography="s1" fontWeight="fontWeightMedium">
        No logs available
      </Typography>
      <Typography typography="s1" fontWeight="fontWeightRegular">
        {"The system didn't capture any logs during this interaction."}
      </Typography>
    </Box>
  );
};

const CallDetailLogGrid = forwardRef(
  ({ callLogId, vapiId, module, callLogs }, ref) => {
    const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
    const useClientSide = Boolean(callLogs);
    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        sortable: false,
        filter: false,
        resizable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        cellStyle: {
          lineHeight: 1,
          padding: "8px",
          display: "flex",
          alignItems: "center",
          height: "100%",
        },
      }),
      [],
    );

    const queryClient = useQueryClient();
    const [logDetailDrawerOpen, setLogDetailDrawerOpen] = useState(null);
    const gridApi = useRef(null);

    const { search, level, category } = useCallLogsSearchStoreShallow((s) => ({
      search: s.search,
      level: s.level,
      category: s.category,
    }));

    useEffect(() => {
      return () => {
        if (!useClientSide) {
          queryClient.removeQueries({ queryKey: ["call-detail-logs"] });
        }
        useCallLogsSearchStore.getState().reset();
      };
    }, [queryClient, useClientSide]);

    // Client-side filtered rows for callLogs from span_attributes
    const clientRows = useMemo(() => {
      if (!useClientSide) return [];
      let rows = callLogs || [];

      if (search) {
        const q = search.toLowerCase();
        rows = rows.filter(
          (r) =>
            (r.body && r.body.toLowerCase().includes(q)) ||
            (r.category && r.category.toLowerCase().includes(q)) ||
            (r.severityText && r.severityText.toLowerCase().includes(q)) ||
            (r.severity_text && r.severity_text.toLowerCase().includes(q)) ||
            JSON.stringify(r.payload || r.attributes || "")
              .toLowerCase()
              .includes(q),
        );
      }
      if (level) {
        rows = rows.filter(
          (r) =>
            (r.severityText || r.severity_text || "").toUpperCase() ===
            level.toUpperCase(),
        );
      }
      if (category) {
        rows = rows.filter(
          (r) => (r.category || "").toLowerCase() === category.toLowerCase(),
        );
      }

      return rows;
    }, [useClientSide, callLogs, search, level, category]);

    // Update totalCount for client-side mode
    useEffect(() => {
      if (useClientSide) {
        useCallLogsSearchStore.setState({ totalCount: clientRows.length });
      }
    }, [useClientSide, clientRows.length]);

    const baseQueryKey = useMemo(() => {
      if (module === "simulate") {
        return ["call-detail-logs", callLogId, search, level, category];
      } else {
        return ["call-detail-logs", vapiId, search, level, category];
      }
    }, [callLogId, vapiId, module, search, level, category]);

    const dataSource = useMemo(() => {
      if (useClientSide) return undefined;
      return {
        getRows: async (params) => {
          const { request } = params;
          const pageNumber = Math.floor(request.startRow / 10) + 1;
          const { search, level, category } = useCallLogsSearchStore.getState();

          if (!callLogId && !vapiId) return;

          const queryKey = [...baseQueryKey, pageNumber];
          const id = module === "simulate" ? callLogId : vapiId;
          try {
            const { data } = await queryClient.fetchQuery({
              queryKey,
              queryFn: () =>
                axios.get(endpoints.testExecutions.getDetailLogs(id), {
                  params: {
                    page: pageNumber,
                    search,
                    ...(level ? { severity_text: level } : {}),
                    ...(category ? { category } : {}),
                    ...(module === "project" ? { vapi_call_id: vapiId } : {}),
                  },
                }),
              staleTime: Infinity,
              gcTime: Infinity,
              meta: {
                errorHandled: true,
              },
            });
            const rows = data?.results?.results || [];
            const totalCount = data?.count || 0;

            useCallLogsSearchStore.setState({ totalCount });

            // Calculate the last row index
            let lastRow = -1;
            if (
              rows.length < 10 ||
              request.startRow + rows.length >= totalCount
            ) {
              lastRow = request.startRow + rows.length;
            }
            if (gridApi?.current) {
              gridApi?.current.hideOverlay();
            }

            params.success({
              rowData: rows,
              rowCount: lastRow,
            });
          } catch (error) {
            logger.warn("Error fetching call detail logs:", error);
            // Show the error overlay
            params.success({
              rowData: [],
              rowCount: 0,
            });
            useCallLogsSearchStore.setState({ totalCount: 0 });
            if (gridApi?.current) {
              gridApi?.current.showNoRowsOverlay();
            }
          }
        },
      };
    }, [
      queryClient,
      callLogId,
      vapiId,
      module,
      gridApi,
      baseQueryKey,
      useClientSide,
    ]);

    // Normalize callLogs keys from snake_case to camelCase for the grid
    const normalizedClientRows = useMemo(() => {
      if (!useClientSide) return [];
      return clientRows.map((row) => ({
        id: row.id,
        loggedAt: row.loggedAt || row.logged_at,
        level: row.level,
        severityText: row.severityText || row.severity_text,
        category: row.category,
        body: row.body,
        attributes: row.attributes,
        payload: row.payload,
      }));
    }, [useClientSide, clientRows]);

    return (
      <Box
        className="ag-theme-quartz"
        style={{
          height: "400px",
          backgroundColor: "var(--bg-paper)",
        }}
      >
        <AgGridReact
          theme={agTheme}
          ref={ref}
          columnDefs={CallDetailLogColumnDefs}
          defaultColDef={defaultColDef}
          paginationPageSizeSelector={false}
          {...(useClientSide
            ? {
                rowData: normalizedClientRows,
              }
            : {
                rowModelType: "serverSide",
                suppressServerSideFullWidthLoadingRow: true,
                serverSideDatasource: dataSource,
                maxBlocksInCache: 10,
                cacheBlockSize: 10,
              })}
          rowStyle={{ cursor: "pointer" }}
          getRowId={({ data }) => data.id}
          pagination={false}
          suppressScrollOnNewData={true}
          rowHeight={55}
          onCellClicked={(params) => {
            setLogDetailDrawerOpen({
              data: params.data,
              rowIndex: params.rowIndex,
            });
          }}
          onGridReady={(params) => {
            gridApi.current = params.api;
          }}
          noRowsOverlayComponent={CustomErrorOverlay}
        />
        <LogDetailDrawer
          open={Boolean(logDetailDrawerOpen)}
          onClose={() => setLogDetailDrawerOpen(null)}
          logDetail={logDetailDrawerOpen}
          setLogDetail={setLogDetailDrawerOpen}
          callLogId={callLogId}
          vapiId={vapiId}
          module={module}
          baseQueryKey={baseQueryKey}
          callLogs={useClientSide ? normalizedClientRows : undefined}
        />
      </Box>
    );
  },
);

CallDetailLogGrid.displayName = "CallDetailLogGrid";
CallDetailLogGrid.propTypes = {
  callLogId: PropTypes.string,
  vapiId: PropTypes.string,
  module: PropTypes.string,
  callLogs: PropTypes.array,
};
export default CallDetailLogGrid;
