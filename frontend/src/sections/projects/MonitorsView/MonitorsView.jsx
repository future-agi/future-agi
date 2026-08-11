import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Box,
  Typography,
  Grid,
  Divider,
  Button,
  Link,
  useTheme,
} from "@mui/material";
import { useDebounce } from "src/hooks/use-debounce";
import Iconify from "src/components/iconify";
import { useSnackbar } from "notistack";
import { useForm } from "react-hook-form";
import axiosInstance, { endpoints } from "src/utils/axios";
import { Events, trackEvent } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
import { Helmet } from "react-helmet-async";
import Image from "src/components/image";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useParams } from "react-router";

import DuplicateMonitor from "./DuplicateMonitor";
import DeleteConfirmation from "./DeleteConfirmation";
import MonitorGrid from "./MonitorGrid";
import AddNewMonitor from "./AddNewMonitor";
import logger from "src/utils/logger";

const MonitorsView = () => {
  const { observeId } = useParams();
  const [showAddMonitor, setShowAddMonitor] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const [selectedRowsData, setSelectedRowsData] = useState([]);
  const gridRef = useRef(null);
  const { enqueueSnackbar } = useSnackbar();
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [duplicateModalOpen, setDuplicateModalOpen] = useState(false);
  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid },
  } = useForm({ mode: "onChange" });
  const [selectedAll, setSelectedAll] = useState(false);
  const [isDataEmpty, setIsDataEmpty] = useState(false);
  const [isEditingAlertId, setIsEditingAlertId] = useState("");
  const [alertData, setAlertData] = useState(null);
  const theme = useTheme();
  const queryClient = useQueryClient();
  const { setHeaderConfig } = useObserveHeader();

  const refreshGrid = useCallback(() => {
    trackEvent(Events.pObserveRefreshClicked);
    if (gridRef.current) {
      gridRef.current?.api?.refreshServerSide();
    }
    queryClient.invalidateQueries({
      queryKey: ["monitor-list"],
    });
  }, [queryClient]);

  useEffect(() => {
    setHeaderConfig({
      text: "Monitors",
      refreshData: refreshGrid,
    });
  }, [refreshGrid, setHeaderConfig]);

  // Reset state when project changes
  useEffect(() => {
    if (observeId) {
      setIsDataEmpty(false); // Reset to false, let the grid determine the actual state
      setSelectedRowsData([]);
      setSelectedAll(false);
      setSearchQuery("");
      setShowAddMonitor(false);
      setIsEditingAlertId("");
      setAlertData(null);

      // Refresh the grid when project changes
      if (gridRef.current) {
        gridRef.current?.api?.refreshServerSide();
      }
    }
  }, [observeId]);

  const handleSelectionChanged = (event) => {
    if (!event) {
      setTimeout(() => {
        setSelectedRowsData([]);
      }, 300);
      gridRef?.current?.api?.deselectAll();
      return;
    }
    if (event?.data?.id) {
      const rowId = event?.data?.id;
      setSelectedRowsData((prevSelectedItems) => {
        const updatedSelectedRowsData = [...prevSelectedItems];

        const rowIndex = updatedSelectedRowsData?.findIndex(
          (row) => row?.id === rowId,
        );

        if (rowIndex === -1) {
          updatedSelectedRowsData.push(event.data);
        } else {
          updatedSelectedRowsData.splice(rowIndex, 1);
        }

        return updatedSelectedRowsData;
      });
    }
  };

  const isDark = theme.palette.mode === "dark";
  const monitorsInstruction = [
    {
      title: "Select the Metrics",
      description:
        "Choose from preset evaluations and simplify your decisions instantly!",
      image: isDark
        ? "/assets/images/monitors/metrics_dark.png"
        : "/assets/images/monitors/metrics.png",
    },

    {
      title: "Define the Data",
      description:
        "Configure them once, save as templates, and reuse effortlessly whenever you need.",
      image: isDark
        ? "/assets/images/monitors/data_dark.png"
        : "/assets/images/monitors/data.png",
    },

    {
      title: "Define the Alert",
      description:
        "Ensure accuracy and reliability by running a test before applying changes.",
      image: isDark
        ? "/assets/images/monitors/alerts_dark.png"
        : "/assets/images/monitors/alerts.png",
    },
  ];

  const clearSelection = () => {
    if (gridRef.current) {
      gridRef.current?.api?.deselectAll();
    }
    setSelectedAll(false);
    handleSelectionChanged(null);
  };
  const handleDelete = () => {
    setDeleteModalOpen(true);
  };

  const confirmDelete = async () => {
    if (gridRef.current) {
      gridRef.current?.api?.deselectAll();
      try {
        const ids = selectedRowsData.map((row) => row.id);

        await axiosInstance.delete(endpoints.project.createMonitor, {
          data: { ids: ids },
        });
        const filesLength = ids.length;
        const message =
          filesLength === 1
            ? "Monitor has been deleted."
            : `${filesLength} Monitors have been deleted.`;
        setDeleteModalOpen(false);
        enqueueSnackbar(message, {
          variant: "success",
        });
        gridRef?.current?.api?.refreshServerSide();
        setSelectedRowsData([]);
      } catch (error) {
        enqueueSnackbar(
          error?.message ||
            "An unexpected error occurred while deleting monitors.",
          {
            variant: "error",
          },
        );
      }
    }
  };
  const toggleDialog = () => {
    reset();
    setDuplicateModalOpen((prev) => !prev);
  };

  const onSubmit = async (data) => {
    if (gridRef.current) {
      gridRef.current?.api?.deselectAll();
      setDuplicateModalOpen(false);
      const payload = {
        id: selectedRowsData[0].id,
        name: data.monitorName,
      };
      try {
        await axiosInstance.post(
          endpoints.project.duplicateMonitorList(),
          payload,
        );
        enqueueSnackbar(`${data.monitorName} has been duplicated`, {
          variant: "success",
        });
        gridRef?.current?.api?.refreshServerSide();
      } catch (error) {
        enqueueSnackbar(error.message, {
          variant: "error",
        });
        logger.error("Failed to duplicate monitor", error);
      }
    }
  };

  const onBack = () => {
    setShowAddMonitor(false);
    setAlertData(null);
    setIsEditingAlertId("");
  };

  const { data: alertInfo, isPending: isLoadingAlertData } = useQuery({
    queryKey: ["alert-data", isEditingAlertId],
    queryFn: () =>
      axiosInstance
        .get(endpoints.project.createMonitor + isEditingAlertId + "/")
        .then((res) => res.data.result),
    enabled: !!isEditingAlertId,
    select: (data) => data,
  });

  useEffect(() => {
    if (alertInfo) {
      setAlertData(alertInfo);
      setIsDataEmpty(false);
      setShowAddMonitor(true);
    }
  }, [alertInfo]);

  if (showAddMonitor) {
    return (
      <AddNewMonitor
        onBack={onBack}
        setIsDataEmpty={setIsDataEmpty}
        isDataEmpty={isDataEmpty}
        alertData={alertData}
        isLoadingAlertData={isLoadingAlertData}
      />
    );
  }

  return (
    <Box
      sx={{
        paddingX: theme.spacing(2),
        paddingY: theme.spacing(2),
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Helmet>
        <title>Observe - Alerts</title>
      </Helmet>
      <Box display={"flex"} flexDirection={"column"} gap={theme.spacing(2)}>
        {isDataEmpty ? (
          <Box>
            <Grid container justifyContent="center">
              {monitorsInstruction.map((monitor, index) => (
                <Grid
                  item
                  xs={12}
                  sm={4}
                  key={index}
                  sx={{ textAlign: "left" }}
                >
                  <Box
                    paddingX={theme.spacing(1.5)}
                    display={"flex"}
                    flexDirection={"column"}
                  >
                    <Image
                      src={monitor.image}
                      alt={monitor.title}
                      ratio="4/3"
                      sx={{
                        minHeight: 300,
                        borderRadius: theme.spacing(2),
                      }}
                    />
                    <Typography
                      typography="m2"
                      fontWeight={"fontWeightSemiBold"}
                      color="text.primary"
                      mt={theme.spacing(2)}
                    >
                      {monitor.title}
                    </Typography>
                    <Typography
                      typography="m3"
                      fontWeight={"fontWeightRegular"}
                      color="text.disabled"
                    >
                      {monitor.description}
                    </Typography>
                  </Box>
                </Grid>
              ))}
            </Grid>
            <Box sx={{ marginTop: theme.spacing(3) }}>
              <Divider />
            </Box>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                textAlign: "center",
                width: "100%",
                marginTop: theme.spacing(3),
              }}
            >
              <Typography
                color="text.secondary"
                typography="m3"
                fontWeight="fontWeightSemiBold"
                sx={{ marginBottom: theme.spacing(2) }}
              >
                For more instructions, check out our{" "}
                <Link
                  href="https://docs.futureagi.com/docs/observe/features/alerts"
                  underline="always"
                  color="primary.main"
                  target="_blank"
                >
                  Docs
                </Link>
              </Typography>
              <Button
                variant="contained"
                color="primary"
                onClick={() => {
                  setShowAddMonitor(true);
                  setIsDataEmpty(false);
                }}
              >
                <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                  Start Creating Alerts
                </Typography>
              </Button>
            </Box>
          </Box>
        ) : (
          <>
            <Box
              sx={{
                width: "100%",
                display: "flex",
                justifyContent: "space-between",
                height: "40px",
              }}
            >
              <FormSearchField
                size="small"
                placeholder="Search"
                sx={{ minWidth: "30%" }}
                searchQuery={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {selectedRowsData.length > 0 ? (
                <Box
                  sx={{
                    display: "flex",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "4px",
                    alignItems: "center",
                    gap: 1.5,
                    px: 1.5,
                    height: "38px",
                  }}
                >
                  <Typography
                    fontWeight="fontWeightMedium"
                    typography="s1"
                    color="primary.main"
                  >
                    {selectedRowsData.length} Selected
                  </Typography>
                  <Divider
                    orientation="vertical"
                    flexItem
                    sx={{ width: "1px", height: "20px", mt: 1 }}
                  />
                  {selectedRowsData.length === 1 && (
                    <Button
                      startIcon={
                        <SvgColor
                          src="/assets/icons/ic_copy.svg"
                          sx={{ width: 20, height: 20, color: "text.disabled" }}
                        />
                      }
                      size="small"
                      onClick={toggleDialog}
                      disabled={selectedRowsData.length !== 1}
                    >
                      <Typography
                        typography="s1"
                        fontWeight={"fontWeightRegular"}
                        color="text.primary"
                      >
                        Duplicate as new
                      </Typography>
                    </Button>
                  )}
                  <Button
                    startIcon={
                      <SvgColor
                        src="/assets/icons/ic_delete.svg"
                        sx={{ width: 20, height: 20, color: "text.primary" }}
                      />
                    }
                    size="small"
                    sx={{ color: "text.primary", fontWeight: 400 }}
                    onClick={handleDelete}
                  >
                    <Typography
                      typography="s1"
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Delete
                    </Typography>
                  </Button>
                  <Divider
                    orientation="vertical"
                    flexItem
                    sx={{ width: "1px", height: "20px", mt: 1 }}
                  />
                  <Button
                    size="small"
                    sx={{ color: "text.primary", fontWeight: 400, mx: -1 }}
                    onClick={clearSelection}
                  >
                    <Typography
                      typography="s1"
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Cancel
                    </Typography>
                  </Button>
                </Box>
              ) : (
                <Button
                  variant="contained"
                  size="medium"
                  color="primary"
                  onClick={() => {
                    setShowAddMonitor(true);
                    trackEvent(Events.createNewMonitorClicked);
                  }}
                  sx={{ height: "38px", width: "190px" }}
                  startIcon={
                    <Iconify
                      icon="fe:plus"
                      color="background.paper"
                      sx={{
                        width: "20px",
                        height: "20px",
                      }}
                    />
                  }
                >
                  <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                    Create new alert
                  </Typography>
                </Button>
              )}
            </Box>
            <MonitorGrid
              ref={gridRef}
              onSelectionChanged={handleSelectionChanged}
              searchQuery={debouncedSearchQuery}
              setSelectedRowsData={setSelectedRowsData}
              selectedAll={selectedAll}
              setSelectedAll={setSelectedAll}
              setIsDataEmpty={setIsDataEmpty}
              setIsEditingAlertId={setIsEditingAlertId}
            />
          </>
        )}
      </Box>
      <DeleteConfirmation
        open={deleteModalOpen}
        onClose={() => setDeleteModalOpen(false)}
        onConfirm={confirmDelete}
      />
      <DuplicateMonitor
        open={duplicateModalOpen}
        onClose={toggleDialog}
        control={control}
        handleSubmit={handleSubmit}
        onSubmit={onSubmit}
        isValid={isValid}
      />
    </Box>
  );
};

export default MonitorsView;
