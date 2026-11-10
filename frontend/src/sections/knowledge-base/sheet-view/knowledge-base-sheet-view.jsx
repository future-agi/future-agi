import { Box, Tab, Tabs, useTheme } from "@mui/material";
import React, { useEffect, useRef, useState } from "react";
import NavSection from "./nav-section";
import Actions from "./actions";
import SheetTableView from "./sheet-table-view";
import { ShowComponent } from "src/components/show";
import ConfirmDelete from "./confirm-delete";
import { enqueueSnackbar } from "notistack";
import Status from "./status";
import { useDebounce } from "src/hooks/use-debounce";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import CreateKnowledgeBaseDrawer from "../CreateKnowledgeBase/CreateKnowledgeBaseDrawer";
import EditKnowledgeBaseNameDialog from "./edit-knowledge-base-name-dialog";
import SyntheticDataDrawer from "src/sections/develop/AddRowDrawer/CreateSyntheticData";
import PdfPreviewDrawer from "src/components/PdfPreviewDrawer";

const tabOptions = [
  {
    value: "name",
    label: "Name",
  },
  // {
  //   value: "logs",
  //   label: "Logs"
  // }, {
  //   value: "erros",
  //   label: "Errors"
  // },
];

export default function KnowledgeBaseSheetView() {
  const gridRef = useRef(null);
  const theme = useTheme();
  const [selectedAll, setSelectedAll] = useState(false);
  const [openDeleteDialog, setOpenDeleteDialog] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 300);
  const { knowledgeId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [lastUpdatedDate, setLastUpdatedDate] = useState(null);
  const [status, setStatus] = useState({
    status: "",
    status_count: 0,
  });
  const [openAddRow, setOpenAddRow] = useState(null);
  const [selectedknowledgeBaseName, setSelectedknowledgeBaseName] =
    useState(null);
  const [isFetchingData, setIsFetchingData] = useState(false);
  const [openEditDialog, setOpenEditDialog] = useState(false);
  const [syntheticDataDrawerOpen, setSyntheticDataDrawerOpen] = useState(false);
  const allRowSelected = useRef(false);
  const [totalRows, setTotalRows] = useState(0);
  const [excludingIds, setExcludingIds] = useState(new Set());

  const currentTab = searchParams.get("tab") || tabOptions[0]?.value;

  const handleChangeTab = (event, newValue) => {
    setSearchParams({ tab: newValue }, { replace: true }); // Update URL without reloading
  };

  // Ensure `tab` exists in the URL, otherwise set default to first tab
  useEffect(() => {
    if (!searchParams.has("tab")) {
      setSearchParams({ tab: tabOptions[0]?.value }, { replace: true });
    }
    return () => {
      setStatus({
        status: "",
        status_count: 0,
      });
      setLastUpdatedDate(null);
    };
  }, [searchParams, setSearchParams]);

  const handleRemoveSelectedFile = (rowId) => {
    if (!rowId) return;

    const gridApi = gridRef.current?.api;
    if (!gridApi) return;

    if (selectedFiles?.length === 1) {
      gridApi.deselectAll();
      setOpenDeleteDialog(false);
      setSelectedFiles([]);
    }

    gridApi.forEachNode((node) => {
      if (node.isSelected() && node.data.id === rowId) {
        node.setSelected(false); // Deselect this specific row
      }
    });
  };

  const { mutate: deleteSelectedFiles, isPending: isDeleting } = useMutation({
    mutationFn: (deletePayload) =>
      axios.delete(endpoints.knowledge.files, {
        data: {
          kb_id: knowledgeId,
          ...deletePayload,
        },
      }),
    onSuccess: (_, variables) => {
      allRowSelected.current = false;
      const filesLength = variables?.delete_all
        ? totalRows - Array.from(excludingIds).length
        : variables?.file_ids?.length ?? 0;
      const message =
        filesLength === 1
          ? "One file has been deleted."
          : `${filesLength} files have been deleted.`;

      gridRef.current?.api?.deselectAll();
      setSelectedFiles([]);
      setSelectedAll(false);
      gridRef.current?.api?.refreshServerSide();
      enqueueSnackbar(message, { variant: "success" });
      setOpenDeleteDialog(false);
    },
  });

  const onDeleteFiles = async () => {
    const deletePayload = {};
    const ids = selectedFiles.map((file) => file.id);

    if (ids.length === 0 && !selectedAll) {
      enqueueSnackbar("No files selected.", { variant: "warning" });
      return;
    }

    if (selectedAll) {
      deletePayload["delete_all"] = true;
      deletePayload["excluded_file_ids"] = Array.from(excludingIds);
    } else {
      deletePayload["delete_all"] = false;
      deletePayload["file_ids"] = ids;
    }

    deleteSelectedFiles(deletePayload);
  };

  const refreshGrid = () => {
    if (!gridRef?.current) return;
    gridRef?.current?.api?.refreshServerSide();
  };

  const handleCloseEditDialog = () => {
    setOpenEditDialog(false);
  };

  const handleCloseSyntheticDrawer = () => {
    setSyntheticDataDrawerOpen(false);
  };
  const handleOpenSyntheticDrawer = () => {
    setSyntheticDataDrawerOpen(true);
  };

  return (
    <>
      <Box
        sx={{
          minHeight: "100vh",
          background: "background.paper",
          p: "16px",
          display: "flex",
          flexDirection: "column",
          rowGap: "16px",
        }}
      >
        {/* navigation */}
        <NavSection
          refreshGrid={refreshGrid}
          isFetchingData={isFetchingData}
          lastUpdatedDate={lastUpdatedDate}
          setName={setSelectedknowledgeBaseName}
        />

        {/* search and action buttons */}
        <Actions
          excludingIds={Array.from(excludingIds)}
          setTotalRows={setTotalRows}
          selectedAll={selectedAll}
          totalRows={totalRows}
          allRowSelected={allRowSelected}
          setOpenEditDialog={setOpenEditDialog}
          knowledgeId={knowledgeId}
          status={status}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          setOpenDeleteDialog={setOpenDeleteDialog}
          setSelectedFiles={setSelectedFiles}
          selectedFiles={selectedFiles}
          setSelectedAll={setSelectedAll}
          gridRef={gridRef}
          sx={{}}
          setOpenAddRow={() => setOpenAddRow(true)}
          handleOpenSyntheticDrawer={handleOpenSyntheticDrawer}
          handleCloseSyntheticDrawer={handleCloseSyntheticDrawer}
        />

        {/* tasks section */}
        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs
            sx={{
              "& .MuiTab-root": {
                margin: "0 !important",
                fontWeight: "fontWeightSemiBold",
                typography: "s1",
                "&:not(.Mui-selected)": {
                  color: "text.disabled", // Color for unselected tabs
                },
              },
              "&.Mui-selected": {
                color: "primary.main",
                fontWeight: "bold",
              },
            }}
            textColor="primary"
            indicatorColor="primary"
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
              },
            }}
            value={currentTab}
            onChange={handleChangeTab}
            aria-label="basic tabs example"
          >
            {tabOptions.map((option, index) => (
              <Tab
                sx={{
                  margin: 0,
                  px: "24px",
                }}
                key={index}
                label={option.label}
                value={option.value}
              />
            ))}
          </Tabs>
        </Box>
        {/* status */}
        {(status?.status === "Processing" || status?.status === "Deleting") && (
          <Status status={status} />
        )}

        <ShowComponent condition={currentTab === "name"}>
          <SheetTableView
            totalRows={totalRows}
            setExcludingIds={setExcludingIds}
            setTotalRows={setTotalRows}
            setIsFetchingData={setIsFetchingData}
            setStatus={setStatus}
            setLastUpdatedDate={setLastUpdatedDate}
            knowledgeId={knowledgeId}
            debouncedSearchQuery={debouncedSearchQuery}
            ref={gridRef}
            setSelectedFiles={setSelectedFiles}
            setSelectedAll={setSelectedAll}
            selectedAll={selectedAll}
            status={status?.status}
          />
        </ShowComponent>
      </Box>
      <ConfirmDelete
        excludingIds={Array.from(excludingIds)}
        selectedAll={selectedAll}
        totalRows={totalRows}
        onConfirm={onDeleteFiles}
        isLoading={isDeleting}
        selectedFiles={selectedFiles}
        open={openDeleteDialog}
        onClose={() => setOpenDeleteDialog(false)}
        handleRemoveSelectedFile={handleRemoveSelectedFile}
      />
      <CreateKnowledgeBaseDrawer
        open={openAddRow}
        onClose={() => setOpenAddRow(null)}
        refreshGrid={refreshGrid}
        data={{ name: selectedknowledgeBaseName?.label }}
      />
      <EditKnowledgeBaseNameDialog
        knowledgeId={knowledgeId}
        open={openEditDialog}
        onClose={handleCloseEditDialog}
      />
      <SyntheticDataDrawer
        knowledgeId={knowledgeId}
        open={syntheticDataDrawerOpen}
        onClose={() => {
          handleCloseSyntheticDrawer();
        }}
        datasetId={null}
        refreshGrid={refreshGrid}
      />
      <PdfPreviewDrawer />
    </>
  );
}
