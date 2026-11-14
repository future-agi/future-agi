import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import React, { useRef, useState } from "react";
import { useParams } from "src/routes/hooks";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";

import DatasetHeader from "../datasets/DatasetHeader";
import CustomizeColumnsModal from "../datasets/CustomizeColumnsModal";
import OptimizeDetailDrawer from "../optimize/OptimizeDetailDrawer";

import PromptTemplateExploreTable from "./PromptTemplateExploreTable";
import { LoadingScreen } from "src/components/loading-screen";

const PromptTemplateExplore = ({ selectedOptimization }) => {
  const { id } = useParams();

  const datasetHeaderRef = useRef();

  const queryClient = useQueryClient();

  const [isCustomizeColumnOpen, setIsCustomizeColumnOpen] = useState(false);

  const { data: columns } = useQuery({
    queryFn: () =>
      axios.get(
        endpoints.optimization.getPromptTemplateExploreColumns(
          id,
          selectedOptimization.id,
        ),
      ),
    queryKey: ["prompt-template-columns", id, selectedOptimization.id],
    select: (d) => d?.data?.columns,
  });

  const { mutate: updateColumnConfig } = useMutation({
    mutationFn: (d) =>
      axios.post(
        endpoints.optimization.updatePromptTemplateExploreColumns(
          id,
          selectedOptimization.id,
        ),
        d,
      ),
    onSuccess: () => {
      setIsCustomizeColumnOpen(false);
      // trackEvent(Events.optimizeDetailPageEditColumnComplete, {
      //   "Old Column List": context?.previousColumnConfig?.data?.columns,
      //   "New Column List": v?.columns,
      // });
    },
    onMutate: async (newData) => {
      const queryKey = ["prompt-template-columns", id, selectedOptimization.id];
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
        ["prompt-template-columns", id, selectedOptimization.id],
        context.previousColumnConfig,
      );
    },
    onSettled: () => {
      queryClient.invalidateQueries({
        queryKey: ["prompt-template-columns", id, selectedOptimization.id],
      });
    },
  });

  const { isLoading, data, fetchNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: ["prompt-template-explore", id, selectedOptimization.id],
      queryFn: ({ pageParam }) =>
        axios.post(
          endpoints.optimization.getPromptTemplateExplore(
            id,
            selectedOptimization.id,
          ),
          {
            page: pageParam,
            limit: 15,
          },
        ),
      initialPageParam: 1,
      getNextPageParam: (o) => {
        return o?.data?.next ? o?.data?.current_page + 1 : null;
      },
    });

  const datasetData = data?.pages?.reduce(
    (acc, curr) => [...acc, ...curr.data.results],
    [],
  );

  const [selectedRow, setSelectedRow] = useState(null);

  // const { data: datasetOptions } = useGetMetricOptions(id);

  // const [filterOpen, setFilterOpen] = useState(false);

  // const addFilter = () => {
  //   setFilters((f) => [...f, defaultFilter()]);
  // };

  // const removeFilter = (idx) => {
  //   setFilters((f) => f.filter((_, id) => id !== idx));
  // };

  const onEditColumClick = () => {
    setIsCustomizeColumnOpen(true);
    // trackEvent(Events.optimizeDetailPageEditColumnStart);
  };

  return (
    <>
      <DatasetHeader
        ref={datasetHeaderRef}
        // filterOpen={filterOpen}
        // setFilterOpen={setFilterOpen}
        onEditColumnClick={onEditColumClick}
        // rightSection={renderRightSection()}
        // onSortClick={() => setSortOpen(true)}
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
          saveLoading={false}
        />
      ) : null}
      <OptimizeDetailDrawer
        open={Boolean(selectedRow)}
        onClose={() => setSelectedRow(null)}
        selectedRow={selectedRow}
        columns={columns}
      />
      {isLoading ? <LoadingScreen compact /> : null}
      {/* {!isLoading && !datasetData?.length && <DatasetNoData />} */}
      {!isLoading && Boolean(datasetData?.length) && (
        <PromptTemplateExploreTable
          datasetPointData={datasetData}
          isLoading={isLoading}
          fetchNextPage={fetchNextPage}
          isFetchingNextPage={isFetchingNextPage}
          columns={columns}
          setSelectedRow={setSelectedRow}
        />
      )}
    </>
  );
};

PromptTemplateExplore.propTypes = {
  selectedOptimization: PropTypes.object,
};

export default PromptTemplateExplore;
