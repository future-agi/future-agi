import React, { useState } from "react";
import { useParams } from "src/routes/hooks";
import { Button } from "@mui/material";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import DatasetHeader from "../datasets/DatasetHeader";
import CustomizeColumnsModal from "../datasets/CustomizeColumnsModal";

import OptimizeDrawer from "./OptimizeDrawer";
import OptimizeTable from "./OptimizeTable";
import NoOptimizationData from "./NoOptimizationData";
import { LoadingScreen } from "src/components/loading-screen";

const OptimizeList = () => {
  const [isCustomizeColumnOpen, setIsCustomizeColumnOpen] = useState(false);
  const [isOptimizeDrawerOpen, setIsOptimizeDrawerOpen] = useState(false);
  const [viewOptimization, setViewOptimization] = useState(null);

  const { id } = useParams();

  const queryClient = useQueryClient();

  const onViewClick = (row) => {
    setViewOptimization({
      name: row.name,
      startDate: new Date(row?.startDate),
      endDate: new Date(row?.endDate),
      environment: row?.environment,
      version: row?.version,
      optimizeType: row?.optimizeType,
      metrics: row?.metrics?.map(({ name, id }) => ({
        label: name,
        value: id,
      })),
    });
    setIsOptimizeDrawerOpen(true);
  };

  const { data: columns } = useQuery({
    queryFn: () => axios.get(endpoints.optimization.getColumns(id)),
    queryKey: ["optimization-table-columns", id],
    select: (d) => d?.data?.columns,
  });

  const { mutate: updateColumnConfig, isPending: updatingColumnConfig } =
    useMutation({
      mutationFn: (d) =>
        axios.post(endpoints.optimization.updateColumns(id), d),
      onSuccess: () => {
        setIsCustomizeColumnOpen(false);
        // trackEvent(Events.optimizePageEditColumnStart, {
        //   "Old Column List": context?.previousColumnConfig?.data?.columns,
        //   "New Column List": v?.columns,
        // });
      },
      onMutate: async (newData) => {
        const queryKey = ["optimization-table-columns", id];
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
          ["optimization-table-columns", id],
          context.previousColumnConfig,
        );
      },
      onSettled: () => {
        queryClient.invalidateQueries({
          queryKey: ["optimization-table-columns", id],
        });
      },
    });

  const { isLoading, data, fetchNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: ["optimization-list", id],
      queryFn: ({ pageParam }) =>
        axios.get(`${endpoints.optimization.getAll}${id}/`, {
          params: {
            page: pageParam,
            // filters: validatedFilters,
            limit: 15,
            // sortOrder: sortOrder,
          },
        }),
      initialPageParam: 1,
      getNextPageParam: (o) => {
        return o?.data?.next ? o?.data?.current_page + 1 : null;
      },
    });

  const optimizationData = data?.pages?.reduce(
    (acc, curr) => [...acc, ...curr.data.results],
    [],
  );

  const noData = !isLoading && !optimizationData?.length;

  const onEditColumnClick = () => {
    // trackEvent(Events.optimizePageEditColumnStart);
    setIsCustomizeColumnOpen(true);
  };

  return (
    <>
      <DatasetHeader
        onEditColumnClick={noData ? undefined : onEditColumnClick}
        rightSection={
          <Button
            variant="contained"
            color="primary"
            sx={{ width: "153px" }}
            size="medium"
            id="add-optimization-button"
            onClick={() => {
              // trackEvent(Events.optimizePageOptimizeDatasetFormOpen);
              setIsOptimizeDrawerOpen(true);
            }}
          >
            Optimize Dataset
          </Button>
        }
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
      <OptimizeDrawer
        open={isOptimizeDrawerOpen}
        onClose={() => {
          setIsOptimizeDrawerOpen(false);
          setViewOptimization(false);
        }}
        viewData={viewOptimization}
      />
      {isLoading ? <LoadingScreen /> : null}
      {!isLoading && !optimizationData?.length && <NoOptimizationData />}
      {!isLoading && Boolean(optimizationData?.length) && (
        <OptimizeTable
          optimizeData={optimizationData}
          isLoading={isLoading}
          fetchNextPage={fetchNextPage}
          isFetchingNextPage={isFetchingNextPage}
          columns={columns}
          onViewClick={onViewClick}
          // datasetSelectMode={datasetSelectMode}
          // selectedDataset={selectedDataset}
          // setSelectedDataset={setSelectedDataset}
          // allSelectedDataset={allSelectedDataset}
          // setAllSelectedDataset={setAllSelectedDataset}
          // totalCount={totalCount}
        />
      )}
    </>
  );
};

OptimizeList.propTypes = {};

export default OptimizeList;
