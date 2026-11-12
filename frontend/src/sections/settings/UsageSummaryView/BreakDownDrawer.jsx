import {
  Box,
  Drawer,
  IconButton,
  Link,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useRef, useState } from "react";
import SvgColor from "src/components/svg-color";
import { fCurrency } from "src/utils/format-number";
import {
  getBreakdownColumnDefs,
  getMonthAndYear,
  usageDefaultColDef,
} from "./common";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { ShowComponent } from "src/components/show";
import axiosInstance, { endpoints } from "../../../utils/axios";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const BREAKDOWN_DRAWER_THEME_PARAMS = {
  headerColumnBorder: {
    width: "0px",
    rowVerticalPaddingScale: 2.6,
  },
};

export default function BreakDownDrawer({
  open,
  onClose,
  selectedWorkspace,
  currentTab,
  selectedMonth,
}) {
  const gridRef = useRef();
  const theme = useTheme();
  const agTheme = useAgThemeWith(BREAKDOWN_DRAWER_THEME_PARAMS);
  const columnDefs = useMemo(() => {
    return getBreakdownColumnDefs(currentTab);
  }, [currentTab]);

  const [totals, setTotals] = useState({
    cost: 0,
    count: 0,
  });

  const getDataSource = useCallback(() => {
    return {
      getRows: async (params) => {
        try {
          if (!selectedMonth || !selectedWorkspace?.id) return;
          const response = await axiosInstance(
            `${endpoints.settings.workspaceUsage}`,
            {
              params: {
                workspace_id: selectedWorkspace?.id,
                ...getMonthAndYear(selectedMonth),
              },
            },
          );

          const result = response?.data?.result;

          // Update totals when data is fetched
          if (result?.total) {
            setTotals({
              cost: result?.total?.cost || 0,
              count: result?.total?.count || 0,
            });
          }

          params.success({
            rowData: result?.evaluations || [],
            rowCount: result?.evaluations?.length ?? 0,
          });
        } catch (error) {
          params.fail();
        }
      },
    };
  }, [selectedWorkspace?.id, selectedMonth]);

  const onGridReady = useCallback(
    (params) => {
      const dataSource = getDataSource();
      params.api.setGridOption("serverSideDatasource", dataSource);
    },
    [getDataSource],
  );

  const handleClose = () => {
    setTotals({
      cost: 0,
      count: 0,
    });
    onClose();
  };

  const covered = useDrawerCovered(open);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{
        sx: {
          height: "100vh",
          minWidth: "640px",
          position: "fixed",
          zIndex: 2,
          borderRadius: "8px",
          backgroundColor: "background.paper",
          overflow: "visible",
          padding: theme.spacing(2),
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
      sx={{
        zIndex: 1099,
        ...coveredDrawerSx(covered),
      }}
    >
      <Stack direction={"column"} gap={2}>
        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
        >
          <Typography
            typography={"m3"}
            fontWeight={"fontWeightSemiBold"}
            color={"text.primary"}
          >
            Cost breakdown of evaluations for {selectedWorkspace?.name}
          </Typography>
          <IconButton size="small" onClick={handleClose}>
            <SvgColor
              sx={{
                height: 24,
                width: 24,
                color: "text.primary",
              }}
              src="/assets/icons/ic_close.svg"
            />
          </IconButton>
        </Stack>
        <Stack direction={"row"} gap={1} alignItems={"center"}>
          <ShowComponent condition={currentTab === "cost"}>
            <SvgColor
              src="/assets/icons/ic_dollar.svg"
              sx={{
                color: "text.primary",
                height: "20px",
                width: "20px",
              }}
            />
          </ShowComponent>
          <Typography
            typography={"m3"}
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            {currentTab === "cost"
              ? `Total Cost: ${fCurrency(totals?.cost ?? 0, true)}`
              : `Total count: ${totals?.count}`}
          </Typography>
        </Stack>
        <Box
          component={"div"}
          className="ag-theme-quartz"
          sx={{
            minHeight: 300,
            maxHeight: 600,
            width: "100%",
          }}
        >
          <AgGridReact
            ref={gridRef}
            columnDefs={columnDefs}
            defaultColDef={usageDefaultColDef}
            rowModelType="serverSide"
            onGridReady={onGridReady}
            suppressServerSideFullWidthLoadingRow={true}
            theme={agTheme}
          />
        </Box>
        <Stack direction={"row"} gap={1}>
          <SvgColor
            sx={{
              height: 20,
              width: 20,
              color: "status.info.fg",
            }}
            src="/assets/icons/ic_info.svg"
          />
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            Need assistance with your evaluation costs? Email us at&nbsp;
            <Link href="mailto:support@futureagi.com">
              support@futureagi.com
            </Link>
            .
          </Typography>
        </Stack>
      </Stack>
    </Drawer>
  );
}

BreakDownDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedWorkspace: PropTypes.object,
  currentTab: PropTypes.oneOf(["count", "cost"]),
  selectedMonth: PropTypes.string,
};
