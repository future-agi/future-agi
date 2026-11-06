import { Button, LinearProgress } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate, useParams } from "react-router-dom";
import { useGetMetricOptions } from "src/api/model/metric";
import ConfigureMetricModal from "src/pages/dashboard/models/ConfigureMetricModal";
import { getRandomId } from "src/utils/utils";
import { validateDatasetFilter } from "src/utils/datasetUtils";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useSnackbar } from "src/components/snackbar";
import { EnvironmentMapper } from "src/utils/constant";
import { useGetModelDetail } from "src/api/model/model";

import OptimizeDrawer from "../optimize/OptimizeDrawer";

import DatasetTable from "./DatasetTable";
import { DatasetNoData } from "./DatasetNoData";
import DatasetHeader from "./DatasetHeader";
import DatasetFilter from "./DatasetFilter/DatasetFilter";
import CustomizeColumnsModal from "./CustomizeColumnsModal";
import AddDatasetModel from "./AddDatasetModel";
import DataMergeSelection from "./DataMergeSelection";
import { DatasetCreateValidation } from "./validation";
import DatasetSort from "./DatasetSort";

const filterProperties = [
  { value: "environment", label: "Environment", dataType: "string" },
  { value: "version", label: "Version", dataType: "string" },
  { value: "volume", label: "Volume", dataType: "number" },
];

const defaultFilter = () => ({
  id: getRandomId(),
  key: "",
  dataType: "",
  value: [],
  operator: "",
});

const Datasets = () => {
  const { id } = useParams();

  const { data: modelDetails } = useGetModelDetail(id);

  // @ts-ignore
  const isMetricAdded = modelDetails?.isMetricAdded;
  // @ts-ignore
  const isDatasetAdded = modelDetails?.isDatasetAdded;
  // @ts-ignore
  const modelType = modelDetails?.modelType;

  const datasetHeaderRef = useRef();

  const queryClient = useQueryClient();

  const { enqueueSnackbar } = useSnackbar();

  const [isDefineMetricOpen, setIsDefineMetricOpen] = useState(
    () => isDatasetAdded && !isMetricAdded,
  );

  const [sortOrder, setSortOrder] = useState("desc");

  const [isCustomizeColumnOpen, setIsCustomizeColumnOpen] = useState(false);

  const [filters, setFilters] = useState([defaultFilter()]);

  const [addDatasetOpen, setAddDatasetOpen] = useState(false);

  const [selectedDataset, setSelectedDataset] = useState([]);

  const [allSelectedDataset, setAllSelectedDataset] = useState(false);

  const [datasetSelectMode, setDatasetSelectMode] = useState(null);

  const [isSortOpen, setSortOpen] = useState(false);

  const navigate = useNavigate();

  const { data: columns } = useQuery({
    queryFn: () => axios.get(`${endpoints.dataset.getColumns}${id}/`),
    queryKey: ["table-columns", id],
    select: (d) => d?.data?.columns,
  });

  const { control, formState, handleSubmit, reset } = useForm({
    defaultValues: {
      environment: "",
      version: "",
    },
    mode: "onChange",
    resolver: zodResolver(DatasetCreateValidation),
  });

  const resetSelectionState = () => {
    setSelectedDataset([]);
    setAllSelectedDataset(false);
    setDatasetSelectMode(false);
    reset();
  };

  const { mutate: updateColumnConfig, isPending: updatingColumnConfig } =
    useMutation({
      mutationFn: (d) =>
        axios.post(`${endpoints.dataset.updateColumns}${id}/`, d),
      onSuccess: () => {
        // trackEvent(Events.datasetsPageEditColumnComplete, {
        //   // @ts-ignore
        //   "New Column List": v?.columns,
        // });
        setIsCustomizeColumnOpen(false);
      },
      onMutate: async (newData) => {
        const queryKey = ["table-columns", id];
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
          ["table-columns", id],
          context.previousColumnConfig,
        );
      },
      onSettled: () => {
        queryClient.invalidateQueries({ queryKey: ["table-columns", id] });
      },
    });

  const { mutate: createDataset } = useMutation({
    mutationFn: (d) =>
      axios.post(`${endpoints.dataset.createDataset}${id}/`, d),
    onSuccess: (d, variables) => {
      enqueueSnackbar({
        message: "Dataset added successfully",
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

  const validatedFilters = useMemo(
    () => filters.reduce(validateDatasetFilter, []),
    [filters],
  );

  // useEffect(() => {
  //   trackEvent(Events.datasetsPageFilterApplied, {
  //     "Filter Key": validatedFilters?.map((v) => v.key),
  //   });
  // }, [validatedFilters]);

  useEffect(() => {
    if (selectedDataset.length) {
      setSelectedDataset([]);
    }
    if (allSelectedDataset) {
      setAllSelectedDataset(false);
    }
  }, [validatedFilters]);

  const { isLoading, data, fetchNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: ["datasets", validatedFilters, sortOrder],
      queryFn: ({ pageParam }) =>
        axios.post(`${endpoints.dataset.list}${id}/`, {
          page: pageParam,
          filters: validatedFilters,
          limit: 15,
          sortOrder: sortOrder,
        }),
      initialPageParam: 1,
      getNextPageParam: (o) => {
        return o?.data?.next ? o?.data?.currentPage + 1 : null;
      },
    });

  const datasetData = data?.pages?.reduce(
    (acc, curr) => [...acc, ...curr.data.results],
    [],
  );

  const totalCount = data?.pages?.[0]?.data?.count;

  const { data: datasetOptions } = useGetMetricOptions(id);

  const [filterOpen, setFilterOpen] = useState(false);

  const addFilter = () => {
    setFilters((f) => [...f, defaultFilter()]);
  };

  const removeFilter = (idx) => {
    if (idx === 0) {
      return setFilters([defaultFilter()]);
    }
    setFilters((f) => f.filter((_, id) => id !== idx));
  };

  const isError = useMemo(
    () => Boolean(Object.keys(formState.errors).length),
    [formState],
  );

  const onCreateForm = (formValues) => {
    //@ts-ignore
    createDataset({
      environment: formValues.environment,
      version: formValues.version,
      selectAll: allSelectedDataset,
      selectedDatasets: selectedDataset,
    });
  };

  const [isOptimizeDrawerOpen, setIsOptimizeDrawerOpen] = useState(false);

  const initialOptimizeData = useMemo(() => {
    if (!datasetSelectMode) {
      return null;
    }
    if (datasetSelectMode === "addDataset" && !selectedDataset.length) {
      return null;
    }
    const foundDataset = selectedDataset?.[0]?.split("-");
    const found = datasetData.find(
      (d) =>
        d.version === foundDataset?.[1] && d.environment === foundDataset?.[0],
    );
    if (!found) {
      return null;
    }
    return {
      startDate: new Date(found.startDate),
      endDate: new Date(found.endDate),
      environment: EnvironmentMapper[found.environment],
      version: found.version,
    };
  }, [datasetSelectMode, selectedDataset]);

  const options = useMemo(() => {
    if (!datasetOptions) {
      return [];
    }
    const envs = new Set(datasetOptions.map((o) => o.environment));

    const environment = Array.from(envs).map((v) => ({
      label: v,
      value: EnvironmentMapper[v],
    }));

    const ver = new Set(datasetOptions.map((o) => o.version));
    const version = Array.from(ver).map((v) => ({ label: v, value: v }));

    return {
      environment,
      version,
    };
  }, [datasetOptions]);

  const renderRightSection = () => {
    if (datasetSelectMode === "addDataset") {
      return (
        <>
          <Button
            variant="contained"
            color="primary"
            size="small"
            disabled={
              (!selectedDataset.length && !allSelectedDataset) || isError
            }
            onClick={handleSubmit(onCreateForm)}
          >
            Create dataset
          </Button>
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            onClick={() => resetSelectionState()}
          >
            Cancel
          </Button>
        </>
      );
    }
    if (datasetSelectMode === "optimizeDataset") {
      if (!selectedDataset.length) {
        return (
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            onClick={() => resetSelectionState()}
          >
            Cancel
          </Button>
        );
      }
      return (
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={!selectedDataset.length}
          onClick={() => {
            // trackEvent(Events.datasetsPageOptimizeDatasetFormOpen, {
            //   "Selected Dataset": selectedDataset?.[0],
            // });
            setIsOptimizeDrawerOpen(true);
          }}
        >
          Optimize dataset
        </Button>
      );
    }
    return <></>;
  };

  const noData = !isLoading && !datasetData?.length;

  const onEditColumnClick = () => {
    // trackEvent(Events.datasetsPageEditColumnStart);
    setIsCustomizeColumnOpen(true);
  };

  const onFilterOpenClick = (v) => {
    // const newValue = typeof v === "function" ? v(filterOpen) : v;
    // if (newValue) {
    //   trackEvent(Events.datasetsPageFilterStart);
    // }
    setFilterOpen(v);
  };

  const onSortOpen = () => {
    // trackEvent(Events.datasetsPageSortStart);
    setSortOpen(true);
  };

  const setSort = (v) => {
    // trackEvent(Events.datasetsPageSortApplied, {
    //   "Sort Key": "endDate",
    //   "Sort Order": v,
    // });
    setSortOrder(v);
  };

  const actionOptions =
    noData || modelType !== "GenerativeLLM"
      ? [
          {
            label: "Add datasets",
            value: "addDatasets",
            onClick: () => {
              // trackEvent(Events.datasetsPageAddDatasetStart);
              setAddDatasetOpen(true);
            },
          },
        ]
      : [
          {
            label: "Add datasets",
            value: "addDatasets",
            onClick: () => {
              // trackEvent(Events.datasetsPageAddDatasetStart);
              setAddDatasetOpen(true);
            },
          },
          {
            label: "Optimize datasets",
            value: "optimizeDatasets",
            onClick: () => {
              // trackEvent(Events.datasetsPageOptimizeDatasetStart);
              setDatasetSelectMode("optimizeDataset");
            },
          },
        ];

  return (
    <>
      {datasetSelectMode === "addDataset" && (
        <DataMergeSelection
          control={control}
          environmentField="environment"
          versionField="version"
        />
      )}
      <OptimizeDrawer
        open={isOptimizeDrawerOpen}
        onClose={() => {
          setIsOptimizeDrawerOpen(false);
          setDatasetSelectMode(false);
          setSelectedDataset([]);
        }}
        initialData={initialOptimizeData}
      />
      <DatasetHeader
        ref={datasetHeaderRef}
        filterOpen={filterOpen}
        setFilterOpen={noData ? undefined : onFilterOpenClick}
        onEditColumnClick={noData ? undefined : onEditColumnClick}
        actionOptions={actionOptions}
        actionId="add-dataset-button"
        rightSection={renderRightSection()}
        onSortClick={noData ? undefined : onSortOpen}
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
      {isCustomizeColumnOpen ? (
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
      ) : null}
      <DatasetSort
        open={isSortOpen}
        onClose={() => setSortOpen(false)}
        anchorEl={datasetHeaderRef?.current?.sortButton}
        sort={sortOrder}
        setSort={setSort}
      />
      <AddDatasetModel
        open={addDatasetOpen}
        onClose={() => setAddDatasetOpen(false)}
        onAddDataset={() => {
          setDatasetSelectMode("addDataset");
          setAddDatasetOpen(false);
        }}
      />
      <ConfigureMetricModal
        open={isDefineMetricOpen}
        onClose={() => setIsDefineMetricOpen(false)}
      />
      {isLoading ? <LinearProgress /> : null}
      {noData ? <DatasetNoData /> : null}
      {!isLoading && Boolean(datasetData?.length) && (
        <DatasetTable
          datasetData={datasetData}
          isLoading={isLoading}
          fetchNextPage={fetchNextPage}
          isFetchingNextPage={isFetchingNextPage}
          columns={columns}
          datasetSelectMode={datasetSelectMode}
          selectedDataset={selectedDataset}
          setSelectedDataset={setSelectedDataset}
          allSelectedDataset={allSelectedDataset}
          setAllSelectedDataset={setAllSelectedDataset}
          totalCount={totalCount}
        />
      )}
    </>
  );
};

Datasets.propTypes = {};

export default Datasets;
