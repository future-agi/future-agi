import React, { useMemo, useRef, useState } from "react";
import DatasetHeader from "../../datasets/DatasetHeader";
import DatasetFilter from "../../datasets/DatasetFilter/DatasetFilter";
import { getRandomId } from "src/utils/utils";
import CustomizeColumnsModal from "../../datasets/CustomizeColumnsModal";
import { useParams } from "src/routes/hooks";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { Button, LinearProgress } from "@mui/material";
import DatasetDetailTable from "./DatasetDetailTable";
import DatasetDetailSort from "../DatasetDetailSort";
import DataMergeSelection from "../../datasets/DataMergeSelection";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { DatasetCreateValidation } from "../validation";
import { useSnackbar } from "src/components/snackbar";
import { validateDatasetFilter } from "src/utils/datasetUtils";
import DataPointDrawer from "./DataPointDrawer";
import { DatasetDetailNoData } from "./DatasetDetailNoData";
import Lightbox from "yet-another-react-lightbox";
import "yet-another-react-lightbox/styles.css";
import { useLocation } from "react-router";
import { LoadingButton } from "@mui/lab";
import { useNavigate } from "react-router";

const defaultFilter = () => ({
  id: getRandomId(),
  key: "",
  dataType: "",
  value: [],
  operator: "",
});

const DatasetDetail = () => {
  const { state } = useLocation();
  const [filterOpen, setFilterOpen] = useState(false);
  const [filters, setFilters] = useState(state?.filters || [defaultFilter()]);
  const [isCustomizeColumnOpen, setIsCustomizeColumnOpen] = useState(false);
  const [isSortOpen, setSortOpen] = useState(false);
  const [sortKey, setSortKey] = useState("createdAt");
  const [sortOrder, setSortOrder] = useState("desc");

  const [selectedDataPoints, setSelectedDataPoints] = useState([]);

  const [allSelectedDataPoints, setAllSelectedDataPoints] = useState(false);

  const [datasetSelectMode, setDatasetSelectMode] = useState(null);

  const { id, dataset } = useParams();

  const datasetHeader = useRef();

  const { enqueueSnackbar } = useSnackbar();

  const addFilter = () => {
    setFilters((f) => [...f, defaultFilter()]);
  };

  const removeFilter = (idx) => {
    if (idx === 0) return setFilters([defaultFilter()]);
    setFilters((f) => f.filter((_, id) => id !== idx));
  };

  const queryClient = useQueryClient();

  const validatedFilters = useMemo(
    () => filters.reduce(validateDatasetFilter, []),
    [filters],
  );

  // useEffect(() => {
  //   trackEvent(Events.datasetDetailPageFilterApplied, {
  //     "Filter Key": validatedFilters?.map((v) => v.key),
  //   });
  // }, [validatedFilters]);

  const { data: columns } = useQuery({
    queryFn: () =>
      axios.get(`${endpoints.dataPoints.getColumns}${id}/${dataset}/`),
    queryKey: ["data-points-table-columns", id, dataset],
    select: (d) => d?.data?.columns,
  });

  const { data: datasetMetrics, isLoading: isLoadingDatasetMetrics } = useQuery(
    {
      queryFn: () =>
        axios.get(`${endpoints.dataPoints.metrics}${id}/${dataset}/`),
      queryKey: ["dataset-metrics", id, dataset],
      select: (d) => d?.data?.metrics,
    },
  );

  const { data: datasetProperties, isLoading: isLoadingDatasetProperties } =
    useQuery({
      queryFn: () =>
        axios.get(`${endpoints.dataset.propertyList}`, {
          params: {
            environment: dataset?.split("-")[0],
            version: dataset?.split("-")[1],
            all_properties: true,
            model_id: id,
          },
        }),
      queryKey: ["dataset-properties", id, dataset],
      select: (d) => d?.data?.result,
    });

  const [viewDataPoint, setViewDataPoint] = useState(null);

  const filterProperties = useMemo(() => {
    const arr = [];

    datasetMetrics?.forEach((metric) => {
      arr.push({
        value: `metric_${metric.id}`,
        label: `${metric.name} Score`,
        dataType: "number",
      });
    });

    datasetProperties?.forEach((property) => {
      arr.push({
        value: `property_${property.id}`,
        label: property.name,
        dataType: "string",
      });
    });

    arr.push({ value: "createdAt", label: "Created At", dataType: "date" });

    return arr;
  }, [datasetMetrics, datasetProperties]);

  const { isLoading, data, fetchNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: [
        "data-points",
        id,
        dataset,
        validatedFilters,
        sortOrder,
        sortKey,
      ],
      queryFn: ({ pageParam }) =>
        axios.post(`${endpoints.dataPoints.list}${id}/${dataset}/`, {
          page: pageParam,
          limit: 15,
          filters: validatedFilters,
          sort_order: sortOrder,
          sort_key: sortKey,
        }),
      initialPageParam: 1,
      getNextPageParam: (o) => {
        return o?.data?.next ? o?.data?.current_page + 1 : null;
      },
    });

  const { mutate: updateColumnConfig, isPending: updatingColumnConfig } =
    useMutation({
      mutationFn: (d) =>
        axios.post(`${endpoints.dataPoints.updateColumns}${id}/${dataset}/`, d),
      onSuccess: () => {
        // trackEvent(Events.datasetDetailPageEditColumnComplete, {
        //   // @ts-ignore
        //   "New Column List": v?.columns,
        // });
        setIsCustomizeColumnOpen(false);
      },
      onMutate: async (newData) => {
        const queryKey = ["data-points-table-columns", id, dataset];
        await queryClient.cancelQueries({ queryKey });
        const previousColumnConfig = queryClient.getQueryData(queryKey);
        queryClient.setQueryData(queryKey, (old) => ({
          ...old,
          data: { ...old.data, ...newData },
        }));
        return { previousColumnConfig };
      },
      onError: (_, __, context) => {
        queryClient.setQueryData(
          ["data-points-table-columns", id, dataset],
          context.previousColumnConfig,
        );
      },
      onSettled: () => {
        queryClient.invalidateQueries({
          queryKey: ["data-points-table-columns", id, dataset],
        });
      },
    });

  const datasetPointData = data?.pages?.reduce(
    (acc, curr) => [...acc, ...curr.data.results],
    [],
  );

  const navigate = useNavigate();

  const { control, formState, handleSubmit, reset } = useForm({
    defaultValues: {
      environment: "",
      version: "",
    },
    mode: "onChange",
    resolver: zodResolver(DatasetCreateValidation),
  });

  const resetSelectionState = () => {
    setSelectedDataPoints([]);
    setAllSelectedDataPoints(null);
    setDatasetSelectMode(false);
    reset();
  };

  const { mutate: createDataset, isPending: isCreatingDataset } = useMutation({
    mutationFn: (d) =>
      axios.post(`${endpoints.dataPoints.create}${id}/${dataset}/`, d),
    onSuccess: (d, variables) => {
      enqueueSnackbar({
        message: "Dataset created successfully",
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["datasets"], type: "all" });
      queryClient.invalidateQueries({
        queryKey: ["dataset-options", id],
        type: "all",
      });
      resetSelectionState();
      navigate(
        `/dashboard/models/${id}/datasets/${variables.environment}-${variables.version}`,
      );
    },
  });

  const isError = useMemo(
    () => Boolean(Object.keys(formState.errors).length),
    [formState],
  );

  const overAllLoading = isLoadingDatasetMetrics || isLoadingDatasetProperties;

  const onCreateForm = (formValues) => {
    //@ts-ignore
    createDataset({
      environment: formValues.environment,
      version: formValues.version,
      selectAll: allSelectedDataPoints,
      selectedIds: selectedDataPoints,
    });
  };

  const createDatasetDisabled =
    (!selectedDataPoints.length && !allSelectedDataPoints) || isError;

  const renderRightSection = () => {
    const elements = [];
    if (datasetSelectMode === "createNew")
      elements.push(
        <LoadingButton
          variant="contained"
          color="primary"
          size="small"
          disabled={createDatasetDisabled}
          onClick={handleSubmit(onCreateForm)}
          loading={isCreatingDataset}
        >
          Create dataset
        </LoadingButton>,
      );
    else if (datasetSelectMode === "existingDataset")
      elements.push(
        <LoadingButton
          variant="contained"
          color="primary"
          size="small"
          disabled={createDatasetDisabled}
          onClick={handleSubmit(onCreateForm)}
          loading={isCreatingDataset}
        >
          Move datapoints
        </LoadingButton>,
      );

    if (datasetSelectMode)
      elements.push(
        <Button
          variant="outlined"
          color="inherit"
          size="small"
          onClick={() => resetSelectionState()}
        >
          Cancel
        </Button>,
      );
    return elements;
  };

  const totalCount = data?.pages?.[0]?.data?.count;

  const [selectedImages, setSelectedImages] = useState(null);

  const onEditColumnClick = () => {
    // trackEvent(Events.datasetDetailPageEditColumnStart);
    setIsCustomizeColumnOpen(true);
  };

  const onFilterOpenClick = (v) => {
    // if (newValue) {
    //   trackEvent(Events.datasetDetailPageFilterStart);
    // }
    setFilterOpen(v);
  };

  const onSortOpen = () => {
    // trackEvent(Events.datasetDetailPageSortStart);
    setSortOpen(true);
  };

  const options = useMemo(() => {
    if (!datasetProperties?.length) return {};
    const opts = {};

    datasetProperties.forEach((property) => {
      opts[`property_${property.id}`] = property.values.map((v) => ({
        label: v,
        value: v,
      }));
    });

    return opts;
  }, [datasetProperties]);

  if (overAllLoading) return <LinearProgress />;

  return (
    <>
      {datasetSelectMode && (
        <DataMergeSelection
          control={control}
          environmentField="environment"
          versionField="version"
          versionSelect={datasetSelectMode === "existingDataset"}
        />
      )}
      <DatasetHeader
        filterOpen={filterOpen}
        setFilterOpen={onFilterOpenClick}
        onEditColumnClick={onEditColumnClick}
        actionOptions={[
          {
            label: "Create new dataset",
            value: "createNewDataset",
            onClick: () => setDatasetSelectMode("createNew"),
          },
          {
            label: "Move to existing dataset",
            value: "existingDataset",
            onClick: () => setDatasetSelectMode("existingDataset"),
          },
        ]}
        onSortClick={onSortOpen}
        ref={datasetHeader}
        // onExportClick={() => {}}
        rightSection={renderRightSection()}
        appliedFilters={validatedFilters?.length}
      />
      <DatasetFilter
        filterOpen={filterOpen}
        setFilterOpen={setFilterOpen}
        properties={filterProperties}
        filters={filters}
        setFilters={setFilters}
        datasetOptions={options}
        addFilter={addFilter}
        removeFilter={removeFilter}
      />
      {isCustomizeColumnOpen && (
        <CustomizeColumnsModal
          open={isCustomizeColumnOpen}
          onClose={() => setIsCustomizeColumnOpen(false)}
          columns={columns}
          onSaveClick={(cols) => {
            // @ts-ignore
            updateColumnConfig({ columns: cols });
          }}
          saveLoading={updatingColumnConfig}
        />
      )}
      <DatasetDetailSort
        anchorEl={datasetHeader?.current?.sortButton}
        open={isSortOpen}
        onClose={() => setSortOpen(false)}
        sortOrder={sortOrder}
        setSortOrder={setSortOrder}
        sortKey={sortKey}
        setSortKey={setSortKey}
        metricList={datasetMetrics}
      />
      <DataPointDrawer
        open={Boolean(viewDataPoint)}
        onClose={() => setViewDataPoint(null)}
        selectedRow={viewDataPoint}
        metricList={datasetMetrics || []}
        setSelectedImages={setSelectedImages}
      />
      {isLoading && <LinearProgress />}
      {!isLoading && !datasetPointData?.length && <DatasetDetailNoData />}
      {!isLoading && Boolean(datasetPointData?.length) && (
        <DatasetDetailTable
          datasetPointData={datasetPointData}
          isLoading={isLoading}
          fetchNextPage={fetchNextPage}
          isFetchingNextPage={isFetchingNextPage}
          columns={columns}
          datasetSelectMode={datasetSelectMode}
          selectedDataPoints={selectedDataPoints}
          setSelectedDataPoints={setSelectedDataPoints}
          allSelectedDataPoints={allSelectedDataPoints}
          setAllSelectedDataPoints={setAllSelectedDataPoints}
          totalCount={totalCount}
          setViewDataPoint={setViewDataPoint}
          setSelectedImages={setSelectedImages}
        />
      )}
      <Lightbox
        open={Boolean(selectedImages)}
        close={() => {
          setSelectedImages(null);
        }}
        slides={selectedImages?.images}
        index={selectedImages?.defaultIdx}
      />
    </>
  );
};

export default DatasetDetail;
