import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  InputAdornment,
  Tab,
  Tabs,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { format } from "date-fns";
import { useQueryClient } from "@tanstack/react-query";
import { ShowComponent } from "src/components/show";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import SvgColor from "src/components/svg-color";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { LogDetailDrawerColumnDefs } from "./common";
import { flattenObject } from "src/utils/utils";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useCallLogsSearchStore } from "./states";

const LogDetailDrawerContent = ({
  logDetail,
  onClose,
  setLogDetail,
  callLogId,
  baseQueryKey,
  vapiId,
  module,
  callLogs,
}) => {
  const theme = useTheme();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const [tabValue, setTabValue] = useState("fields");
  const [isFetching, setIsFetching] = useState(false);
  const queryClient = useQueryClient();
  const useClientSide = Boolean(callLogs);
  const { mergedRows, totalCount } = useMemo(() => {
    if (useClientSide) {
      return { mergedRows: callLogs, totalCount: callLogs.length };
    }
    // Get all queries from the cache
    const queryCache = queryClient.getQueryCache();
    const allQueries = queryCache.getAll();

    const matchingQueries = allQueries.filter((query) => {
      // Ensure both arrays have at least 5 items
      if (
        !Array.isArray(query.queryKey) ||
        query.queryKey.length < 5 ||
        baseQueryKey.length < 5
      ) {
        return false;
      }
      // Compare all 5 items
      for (let i = 0; i < 5; i++) {
        if (query.queryKey[i] !== baseQueryKey[i]) {
          return false;
        }
      }
      return true;
    });

    const totalCount = matchingQueries?.[0]?.state?.data?.data?.count;

    // Extract and merge all rows from matching queries
    const allRows = matchingQueries.flatMap((query) => {
      const data = query.state.data;
      return data?.data?.results?.results || [];
    });

    return { mergedRows: allRows, totalCount: totalCount };
  }, [queryClient, baseQueryKey, isFetching, useClientSide, callLogs]);

  const [search, setSearch] = useState("");
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

  const filteredRows = useMemo(() => {
    const flattenedObject = flattenObject(logDetail?.data);
    if (search.length) {
      return Object.entries(flattenedObject)
        .filter(([key, value]) => {
          return (
            key?.toLowerCase().includes(search.toLowerCase()) ||
            value?.toString()?.toLowerCase().includes(search.toLowerCase())
          );
        })
        .map(([key, value]) => ({
          key,
          value,
        }));
    }
    return Object.entries(flattenedObject).map(([key, value]) => ({
      key,
      value,
    }));
  }, [search, logDetail?.data]);

  const onNext = async () => {
    const nextIndex = logDetail?.rowIndex + 1;
    if (mergedRows.length > nextIndex) {
      setLogDetail({
        data: mergedRows[nextIndex],
        rowIndex: nextIndex,
      });
    } else if (!useClientSide) {
      const { search, level, category } = useCallLogsSearchStore.getState();
      const id = module === "simulate" ? callLogId : vapiId;
      const nextPage = mergedRows.length / 10 + 1;
      setIsFetching(true);
      try {
        const { data } = await queryClient.fetchQuery({
          queryKey: [...baseQueryKey, nextPage],
          queryFn: () =>
            axios.get(endpoints.testExecutions.getDetailLogs(id), {
              params: {
                page: nextPage,
                search,
                ...(level ? { severity_text: level } : {}),
                ...(category ? { category } : {}),
                ...(module === "project" ? { vapi_call_id: vapiId } : {}),
              },
            }),
          staleTime: Infinity,
          gcTime: Infinity,
        });
        const newRows = data?.results?.results || [];
        setLogDetail({
          data: newRows[0],
          rowIndex: nextIndex,
        });
      } finally {
        setIsFetching(false);
      }
    }
  };

  const onPrev = () => {
    const prevIndex = logDetail?.rowIndex - 1;
    if (prevIndex < 0) return;
    if (prevIndex >= 0) {
      setLogDetail({
        data: mergedRows[prevIndex],
        rowIndex: prevIndex,
      });
    }
  };

  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          Event -{" "}
          {format(new Date(logDetail?.data?.loggedAt), "MM/dd/yyyy, hh:mm a")}
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="akar-icons:chevron-left-small" />}
            sx={{
              padding: "4px 12px",
            }}
            onClick={onPrev}
            disabled={logDetail?.rowIndex === 0}
          >
            Prev
          </Button>
          <LoadingButton
            variant="outlined"
            size="small"
            endIcon={<Iconify icon="akar-icons:chevron-right-small" />}
            sx={{
              padding: "4px 12px",
            }}
            onClick={onNext}
            loading={isFetching}
            disabled={totalCount === logDetail?.rowIndex + 1}
          >
            Next
          </LoadingButton>

          <IconButton
            onClick={onClose}
            size="small"
            sx={{
              color: "text.primary",
            }}
          >
            <Iconify icon="akar-icons:cross" />
          </IconButton>
        </Box>
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          height: "100%",
          overflowY: "hidden",
        }}
      >
        <Box
          sx={{
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Tabs
            textColor="primary"
            value={tabValue}
            onChange={(e, value) => setTabValue(value)}
            indicatorColor="primary"
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
              },
            }}
            sx={{
              "& .MuiTab-root": {
                ...theme.typography.s2_1,
              },
            }}
          >
            <Tab value="fields" label="Fields" />
            <Tab value="json" label="JSON" />
          </Tabs>
        </Box>
        <ShowComponent condition={tabValue === "fields"}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 1,
              overflowY: "hidden",
              flex: 1,
            }}
          >
            <FormSearchField
              size="small"
              placeholder="Filter Field"
              searchQuery={search}
              onChange={(e) => {
                setSearch(e.target.value);
              }}
              sx={{
                width: "350px",
                "& .MuiInputBase-input": {
                  paddingY: `${theme.spacing(0.5)}`,
                  paddingRight: `${theme.spacing(0.5)}`,
                },
              }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SvgColor
                      src={`/assets/icons/custom/search.svg`}
                      sx={{
                        width: "20px",
                        height: "20px",
                        color: "text.disabled",
                      }}
                    />
                  </InputAdornment>
                ),
                endAdornment: search && (
                  <InputAdornment position="end">
                    <Iconify
                      icon="mingcute:close-line"
                      onClick={() => {
                        setSearch("");
                      }}
                      sx={{ color: "text.disabled", cursor: "pointer" }}
                    />
                  </InputAdornment>
                ),
              }}
              inputProps={{
                sx: {
                  padding: 0,
                },
              }}
            />
            <Box sx={{ flex: 1, overflowY: "auto" }}>
              <AgGridReact
                theme={agTheme}
                columnDefs={LogDetailDrawerColumnDefs}
                defaultColDef={defaultColDef}
                paginationPageSizeSelector={false}
                getRowId={({ data }) => data.key}
                pagination={false}
                suppressScrollOnNewData={true}
                rowHeight={55}
                domLayout="autoHeight"
                rowData={filteredRows}
              />
            </Box>
          </Box>
        </ShowComponent>
        <ShowComponent condition={tabValue === "json"}>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              padding: "10px",
              borderRadius: 0.5,
              backgroundColor: "background.neutral",
            }}
          >
            <CustomJsonViewer object={logDetail?.data} />
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

LogDetailDrawerContent.propTypes = {
  logDetail: PropTypes.object,
  onClose: PropTypes.func,
  setLogDetail: PropTypes.func,
  callLogId: PropTypes.string,
  baseQueryKey: PropTypes.array,
  vapiId: PropTypes.string,
  module: PropTypes.string,
  callLogs: PropTypes.array,
};

const LogDetailDrawer = ({
  open,
  onClose,
  logDetail,
  setLogDetail,
  callLogId,
  baseQueryKey,
  vapiId,
  module,
  callLogs,
}) => {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      anchor="right"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
          backgroundColor: "background.paper",
          overflow: "visible",
          width: "623px",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <ShowComponent condition={Boolean(logDetail)}>
        <LogDetailDrawerContent
          logDetail={logDetail}
          onClose={onClose}
          setLogDetail={setLogDetail}
          callLogId={callLogId}
          baseQueryKey={baseQueryKey}
          vapiId={vapiId}
          module={module}
          callLogs={callLogs}
        />
      </ShowComponent>
    </Drawer>
  );
};

LogDetailDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  logDetail: PropTypes.object,
  setLogDetail: PropTypes.func,
  callLogId: PropTypes.string,
  baseQueryKey: PropTypes.array,
  vapiId: PropTypes.string,
  module: PropTypes.string,
  callLogs: PropTypes.array,
};

export default LogDetailDrawer;
