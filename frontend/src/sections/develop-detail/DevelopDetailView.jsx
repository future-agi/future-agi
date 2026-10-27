import { Box, Skeleton, useTheme } from "@mui/material";
import { Outlet, useLocation, useParams } from "react-router";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "src/routes/hooks";
import DatasetsSelectRow from "./DatasetsSelectRow";
import DevelopBar from "./DevelopBar/DevelopBar";
import RunPrompt from "./RunPrompt/RunPrompt";
import RunOptimization from "./Optimization/RunOptimization";
import { ShowComponent } from "src/components/show";
import OptimizeTab from "./OptimizationTab/OptimizeTab";
import EditEvaluation from "./Evaluation/EditEvaluation";
import ExperimentTab from "./ExperimentTab/ExperimentTab";
import DevelopDataRightSection from "./DevelopBarRightSection/DevelopDataRightSection";
import DevelopExperimentRightSection from "./DevelopBarRightSection/DevelopExperimentRightSection";
import DatasetSummaryTab from "./DatasetSummaryTab/DatasetSummaryTab";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import AddColumnApiCall from "./AddColumn/AddColumnApiCall/AddColumnApiCall";
import ExtractEntities from "./AddColumn/ExtractEntities/ExtractEntities";
import Classification from "./AddColumn/Classification/Classification";
import ExecuteCode from "./AddColumn/ExecuteCode/ExecuteCode";
import ExtractJsonKey from "./AddColumn/ExtractJsonKey/ExtractJsonKey";
import Retrieval from "./AddColumn/Retrieval/Retrieval";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import CompareData from "./DataTab/CompareData";
import CompareSummaryRightSection from "./DevelopBarRightSection/CompareSummaryRightSection";
import CompareDataRightSection from "./DevelopBarRightSection/CompareDataRightSection";
import CompareSummaryTab from "./DatasetSummaryTab/CompareSummaryTab";
import RunCompareEvaluation from "./Evaluation/RunCompareEvaluation";
import { useDeleteCompare } from "src/api/develop/dataset-compare";
import DevelopBarLeftSection from "./DevelopBarRightSection/DevelopBarLeftSection";
import { Helmet } from "react-helmet-async";
import RunExperimentWithProvider from "./Experiment/RunExperiment";
import DevelopDataV2 from "./DataTab/DevelopDataV2";
import DevelopDetailProvider from "./DevelopDetailProvider";
import { useDevelopDetailContext } from "./Context/DevelopDetailContext";
import DevelopEvaluationDrawer from "./DataTab/DevelopEvaluationDrawer";
import { useUrlState } from "src/routes/hooks/use-url-state";
import ConditionalNodeV2 from "./AddColumn/ConditionalNode/ConditionalNodeV2";
import ExperimentTabHeader from "./ExperimentTab/ExperimentTabHeader";

const TabOptions = [
  { label: "Data", value: "data" },
  { label: "Experiments", value: "experiments" },
  { label: "Optimization", value: "optimization" },
  { label: "Summary", value: "summary" },
];

const DevelopDetailView = () => {
  const { dataset } = useParams();
  const location = useLocation();
  const { pathname } = location;

  // Check if we're in scenario context (using URL params)
  const urlParams = new URLSearchParams(location.search);
  const hideScenarioFeatures = urlParams.get("fromScenario") === "true";

  const { data } = useDevelopDatasetList();

  const currentDataset = data?.find((d) => d.datasetId === dataset)?.name;

  const tabRef = useRef(null);

  const gridApiRef = useRef(null);
  const compareGridApiRef = useRef(null);
  const resizerRef = useRef(null);
  const [compareFromOutside, setCompareFromOutside] = useState(
    location?.state?.compareFromOutside || false,
  );
  const [isCompareDataset, setIsCompareDataset] = useState(
    location?.state?.isCompare == true ? true : false,
  );
  const [openRunCompareEvaluation, setOpenRunCompareEvaluation] =
    useState(false);
  const [rowSelected, setRowSelected] = useState([]);
  const [columns, setColumns] = useState([]);
  const [selectedColumns, setSelectedColumns] = useState([]);
  const [evalsData, setEvalsData] = useState([]);
  const [isCommonColumn, setIsCommonColumn] = useState(
    location?.state?.isCommonColumn,
  );
  const theme = useTheme();
  const [experimentSearch, setExperimentSearch] = useState("");
  const [selectedRowsCount, setSelectedRowsCount] = useState(0);
  const [selectedRowIds, setSelectedRowIds] = useState([]);

  const queryClient = useQueryClient();
  const [baseColumn, setBaseColumn] = useState(
    location?.state?.baseColumn || "",
  );
  const [selectedDatasets, setSelectedDatasets] = useState(
    location?.state?.selectedDatasets || [],
  );
  const [selectedDatasetData, setSelectedDatasetData] = useState(
    location?.state?.selectedDatasetsValues || [],
  );
  const [commonColumn, setCommonColumn] = useState([]);
  const [datasetInfo, setDatasetInfo] = useState([]);
  const [isChooseWinnerSelected, setIsChooseWinnerSelected] = useState(false);
  const [dataAfterChooseWinner, setDataAfterChooseWinner] = useState({});
  const [isChooseWinnerButtonVisible, setIsChooseWinnerButtonVisible] =
    useState(false);
  const [compareColumnDefs, setCompareColumnDefs] = useState([]);

  const { refreshGrid } = useDevelopDetailContext();

  const compareId = useRef(null);
  const { mutate: deleteCompare } = useDeleteCompare();

  const shouldHideDevelopBar = pathname.includes("/preview/");
  const [params] = useSearchParams({
    tab: "data",
  });
  const [currentTab, setCurrentTab] = useUrlState(
    "tab",
    params.tab || TabOptions[0]?.value,
  );

  const SkeletonHeader = () => {
    return <Skeleton width="60%" />;
  };

  const setRefreshColumns = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["dataset-column-config", dataset],
      type: "all",
    });
  }, [queryClient, dataset]);

  // Column Definitions
  const [columnDefs, setColumnDefs] = useState([
    {
      headerName: "Column 1",
      field: "name",
      flex: 1,
      headerComponent: SkeletonHeader,
    },
    {
      headerName: "Column 2",
      field: "numberOfDatapoints",
      flex: 1,
      headerComponent: SkeletonHeader,
    },
    {
      headerName: "Column 3",
      field: "numberOfExperiments",
      flex: 1,
      headerComponent: SkeletonHeader,
    },
    {
      headerName: "Column 4",
      field: "numberOfOptimisations",
      flex: 1,
      headerComponent: SkeletonHeader,
    },
  ]);

  const compareAllColumn = compareColumnDefs.filter(
    (col) => col?.field !== "checkbox",
  );

  const compareRefreshGrid = (options, refreshCols) => {
    compareGridApiRef.current.api.refreshServerSide(options);
    if (refreshCols) setRefreshColumns();
  };

  function reorderColumnDefsBasedOnColumns(columnDefs, columns) {
    const idToIndex = new Map(columns.map((col, index) => [col.id, index]));
    const defMap = new Map(columnDefs.map((def) => [def.field, def]));

    const updatedColumnDefs = [];

    for (let i = 0; i < columns.length; i++) {
      const colId = columns[i].id;
      const def = defMap.get(colId);

      if (def) {
        const updatedDef = {
          ...def,
          col: def.col ? { ...def.col, orderIndex: i } : def.col,
          headerComponentParams: def.headerComponentParams
            ? {
                ...def.headerComponentParams,
                col: {
                  ...def.headerComponentParams.col,
                  orderIndex: i,
                },
              }
            : def.headerComponentParams,
        };
        updatedColumnDefs.push(updatedDef);
      }
    }

    // Add unmatched columnDefs at the end (if any)
    for (const def of columnDefs) {
      if (!idToIndex.has(def.field)) {
        updatedColumnDefs.push(def);
      }
    }

    return updatedColumnDefs;
  }

  useEffect(() => {
    if (!columns?.length || !columnDefs?.length) return;

    const updatedDefs = reorderColumnDefsBasedOnColumns(columnDefs, columns);
    setColumnDefs(updatedDefs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns]);

  // Mutation for deleting annotations
  const { mutate: handleAnnotationDelete } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.annotation.deleteAnnotations, data),
    onSuccess: () => {
      enqueueSnackbar("Annotation deleted successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["annotationList"] });
    },
  });

  const _handleDeleteSelected = () => {
    if (selectedRowIds.length === 0) return;

    handleAnnotationDelete({ annotation_ids: selectedRowIds });
    trackEvent(Events.annDelSuccess, {
      [PropertyName.count]: {
        selectedCount: selectedRowsCount,
        row_Id: selectedRowIds,
        dataset: currentDataset,
      },
    });

    // Reset selection
    setSelectedRowsCount(0);
    setSelectedRowIds([]);
  };

  const _handleCancelSelection = () => {
    if (gridApiRef.current) {
      gridApiRef.current.api.deselectAll();
    }
    setSelectedRowsCount(0);
    setSelectedRowIds([]);
  };

  const deleteCompareFileIfExists = useCallback(
    (force) => {
      if (
        force ||
        (!isCompareDataset && compareId?.current) ||
        (currentTab === "summary" && compareId?.current)
      ) {
        deleteCompare(compareId.current);
        compareId.current = null;
      }
    },
    [currentTab, deleteCompare, isCompareDataset],
  );

  useEffect(() => {
    deleteCompareFileIfExists();
  }, [isCompareDataset, currentTab, deleteCompareFileIfExists]);

  useEffect(() => {
    return () => {
      // delete compare file when component un mounts
      // resetAllStates();
      deleteCompareFileIfExists(Boolean(compareId?.current));
    };
  }, [deleteCompareFileIfExists]);

  useEffect(() => {
    trackEvent(Events.datasetHomePageLodaded, {
      [PropertyName.id]: dataset,
    });
  }, [dataset]);

  const getRightSection = () => {
    if (isCompareDataset && currentTab === "data") {
      return (
        <CompareDataRightSection
          compareId={compareId}
          refreshGrid={refreshGrid}
          onRunEvaluation={() =>
            !hideScenarioFeatures && setOpenRunCompareEvaluation(true)
          }
          columns={columns}
          selectedColumns={selectedColumns}
          setSelectedColumn={setSelectedColumns}
          baseColumn={baseColumn}
          selectedDatasetsValues={selectedDatasets}
          commonColumn={commonColumn}
          datasetInfo={datasetInfo}
          setColumns={setColumns}
        />
      );
    } else if (currentTab === "data" && !shouldHideDevelopBar) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: theme.spacing(2),
            px: theme.spacing(1.5),
            mt: theme.spacing(1),
          }}
        >
          <DevelopBarLeftSection resizerRef={resizerRef} />
          <DevelopDataRightSection
            hideScenarioFeatures={hideScenarioFeatures}
          />
        </Box>
      );
    } else if (currentTab === "experiments") {
      return (
        <DevelopExperimentRightSection
          experimentSearch={experimentSearch}
          setExperimentSearch={setExperimentSearch}
        />
      );
    }
    if (isCompareDataset && currentTab === "summary") {
      return (
        <CompareSummaryRightSection
          evalsData={evalsData}
          experimentSearch={experimentSearch}
          setExperimentSearch={setExperimentSearch}
          selectedDatasets={selectedDatasets}
          baseColumn={baseColumn}
          commonColumn={commonColumn}
          datasetInfo={datasetInfo}
          setIsChooseWinnerSelected={setIsChooseWinnerSelected}
          setDataAfterChooseWinner={setDataAfterChooseWinner}
          isChooseWinnerButtonVisible={isChooseWinnerButtonVisible}
        />
      );
    }
  };

  return (
    <DevelopDetailProvider>
      <Box
        sx={{
          backgroundColor: "background.paper",
          height: "100%",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Helmet>
          <title>Dataset - {currentTab || "data"}</title>
        </Helmet>
        <DatasetsSelectRow
          setCurrentTab={setCurrentTab}
          currentTab={currentTab}
          setIsCompareDataset={setIsCompareDataset}
          setBaseColumnInParent={setBaseColumn}
          baseColumn={baseColumn}
          setSelectedDatasetsValuesInParent={setSelectedDatasets}
          setSelectedDatasetData={setSelectedDatasetData}
          setIsChooseWinnerSelected={setIsChooseWinnerSelected}
          isCompareDataset={isCompareDataset}
          selectedDatasetData={selectedDatasetData}
          compareFromOutside={compareFromOutside}
          setCompareFromOutSide={setCompareFromOutside}
          setIsCommonColumn={setIsCommonColumn}
          setIsChooseWinnerButtonVisible={setIsChooseWinnerButtonVisible}
          shouldHideDevelopBar={shouldHideDevelopBar}
          hideScenarioFeatures={hideScenarioFeatures}
        />

        {!shouldHideDevelopBar && (
          <DevelopBar
            currentTab={currentTab}
            setCurrentTab={setCurrentTab}
            rightSection={getRightSection()}
            rowSelected={rowSelected}
            setRowSelected={setRowSelected}
            isCompareDataset={isCompareDataset}
            tabRef={tabRef}
            setSelectedDatasetData={setSelectedDatasetData}
            setIsCommonColumn={setIsCommonColumn}
            hideScenarioFeatures={hideScenarioFeatures}
          />
        )}
        <ShowComponent condition={currentTab === "data" && !isCompareDataset}>
          {getRightSection()}
        </ShowComponent>
        <Outlet />
        {isCompareDataset ? (
          <ShowComponent condition={currentTab === "data"}>
            <CompareData
              compareId={compareId}
              gridApiRef={compareGridApiRef}
              baseColumn={baseColumn}
              selectedDatasetsValues={selectedDatasets}
              setColumns={setColumns}
              setCommonColumn={setCommonColumn}
              setDatasetInfo={setDatasetInfo}
              selectedColumns={selectedColumns}
              setCompareColumnDefs={setCompareColumnDefs}
              columns={columns}
            />
          </ShowComponent>
        ) : (
          <ShowComponent
            condition={currentTab === "data" && !shouldHideDevelopBar}
          >
            <DevelopDataV2 />
          </ShowComponent>
        )}
        <ShowComponent condition={currentTab === "optimization"}>
          <OptimizeTab />
        </ShowComponent>
        <ShowComponent condition={currentTab === "experiments"}>
          <>
            <ExperimentTabHeader
              tabRef={tabRef}
              setExperimentSearch={setExperimentSearch}
              experimentSearch={experimentSearch}
              rowSelected={rowSelected}
              setRowSelected={setRowSelected}
              setCurrentTab={setCurrentTab}
            />
            <ExperimentTab
              experimentSearch={experimentSearch}
              setRowSelected={setRowSelected}
              ref={tabRef}
              setCurrentTab={setCurrentTab}
            />
          </>
        </ShowComponent>

        <ShowComponent condition={currentTab === "summary"}>
          {isCompareDataset ? (
            <CompareSummaryTab
              selectedDatasetData={selectedDatasetData}
              baseColumn={baseColumn}
              selectedDatasets={selectedDatasets}
              setEvalsData={setEvalsData}
              commonColumn={commonColumn}
              datasetInfo={datasetInfo}
              isChooseWinnerSelected={isChooseWinnerSelected}
              dataAfterChooseWinner={dataAfterChooseWinner}
              setDataAfterChooseWinner={setDataAfterChooseWinner}
              isCommonColumn={isCommonColumn}
              setIsCommonColumn={setIsCommonColumn}
              setIsChooseWinnerButtonVisible={setIsChooseWinnerButtonVisible}
            />
          ) : (
            <DatasetSummaryTab setCurrentTabs={setCurrentTab} />
          )}
        </ShowComponent>
        {!hideScenarioFeatures && (
          <>
            <RunPrompt />
            <DevelopEvaluationDrawer />
            <RunCompareEvaluation
              open={openRunCompareEvaluation}
              onClose={() => setOpenRunCompareEvaluation(false)}
              allColumns={compareAllColumn}
              refreshGrid={refreshGrid}
              datasetId={dataset}
              selectedDatasets={selectedDatasets}
              isCompareDataset={isCompareDataset}
              datasetInfo={datasetInfo}
              commonColumn={commonColumn}
              baseColumn={baseColumn}
              compareRefreshGrid={compareRefreshGrid}
            />
            <RunExperimentWithProvider tabRef={tabRef} />
            <RunOptimization />
            <EditEvaluation />
          </>
        )}
        <AddColumnApiCall />
        <ExtractEntities />
        <ConditionalNodeV2 />
        <Classification />
        <ExecuteCode />
        <ExtractJsonKey />
        <Retrieval />
      </Box>
    </DevelopDetailProvider>
  );
};

export default DevelopDetailView;
