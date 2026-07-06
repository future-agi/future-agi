import React, { useMemo, useState } from "react";
import { Box } from "@mui/material";
import PropTypes from "prop-types";
import { CustomTab, CustomTabs, TabWrapper } from "./SummaryStyle";
import { ShowComponent } from "src/components/show";
import EvalCard from "./EvalsCard/EvalsCard";
import FilterSummary from "./FilterSummary";
import Iconify from "src/components/iconify";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";
import { useParams } from "react-router";
import StyledChip from "./styledChip";
import PropmtCard from "./PromptCard";
import AnnotationCard from "./AnnotationCard";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const tabOptions = [
  { label: "Evals", value: "evals", disabled: false },
  { label: "Prompt", value: "prompt", disabled: false },
  { label: "Annotation", value: "annotation", disabled: false },
];

const DatasetSummaryTab = ({ setCurrentTabs, datasetId, datasetIndex }) => {
  const { dataset } = useParams();
  const [currentTab, setCurrentTab] = useState("evals");
  const [selectedColumn, setSelectedColumn] = useState([]);
  const [appliedFilter, setAppliedFilter] = useState([]);

  const { data } = useGetDatasetDetail(
    dataset,
    {},
    { column_config_only: true },
  );

  const allListItem = data?.column_config?.filter((item) => {
    if (currentTab === "evals") {
      return (
        item.origin_type !== "evaluation" &&
        item.origin_type !== "optimisation" &&
        item.origin_type !== "optimisation_evaluation" &&
        item.origin_type !== "evaluation_reason"
      );
    } else if (currentTab === "prompt") {
      return item.origin_type === "run_prompt";
    } else {
      return false;
    }
  });
  const onFilterChange = (item) => {
    setAppliedFilter((pre) => {
      const newData = pre.some((temp) => temp.id === item.id)
        ? pre.filter((temp) => temp.id !== item.id)
        : [...pre, item];
      const temp = allListItem.filter((item) =>
        newData.some((temp) => temp.id === item.id),
      );
      setSelectedColumn(temp);
      return newData;
    });
  };

  const handleApplyFilter = () => {
    setAppliedFilter([...selectedColumn]);
  };

  const changeTab = (e, value) => {
    setCurrentTab(value);
    setSelectedColumn([]);
    setAppliedFilter([]);
  };

  const { data: evalsSummary } = useQuery({
    queryKey: ["evals-summary", dataset, []],
    queryFn: () => axios.get(endpoints.dataset.evalsSummary(dataset)),
    select: (response) => response?.data?.result || [],
    enabled: Boolean(dataset),
  });

  const updatedEvalsSummary = evalsSummary?.map((e) => {
    return {
      ...e,
      origin_type: "evaluation",
    };
  });
  const columnLists = useMemo(() => {
    const safeAllListItem = Array.isArray(allListItem) ? allListItem : [];
    const safeUpdatedEvalsSummary = Array.isArray(updatedEvalsSummary)
      ? updatedEvalsSummary
      : [];

    if (currentTab === "evals") {
      return [...safeAllListItem, ...safeUpdatedEvalsSummary];
    }

    return safeAllListItem;
  }, [currentTab, allListItem, updatedEvalsSummary]);
  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        padding: "12px",
      }}
    >
      <Box display="flex" justifyContent={"space-between"}>
        <TabWrapper>
          <CustomTabs
            textColor="primary"
            value={currentTab}
            onChange={changeTab}
            TabIndicatorProps={{
              style: {
                opacity: 0,
                height: "100%",
                borderRadius: "8px",
              },
            }}
          >
            {tabOptions.map((tab) => (
              <CustomTab
                key={tab.value}
                label={tab.label}
                value={tab.value}
                disabled={tab.disabled}
              />
            ))}
          </CustomTabs>
        </TabWrapper>

        {currentTab != "annotation" && (
          <FilterSummary
            columnLists={columnLists}
            handleApplyFilter={handleApplyFilter}
            selectedColumn={selectedColumn}
            setSelectedColumn={setSelectedColumn}
            appliedFilter={appliedFilter}
          />
        )}
      </Box>
      <Box sx={{ overflow: "auto", height: "calc(100vh - 200px)" }}>
        <Box display="flex" gap={2} marginBottom={2}>
          {appliedFilter.map((item) => (
            <StyledChip
              key={item.id}
              label={item.name}
              deleteIcon={
                <Iconify
                  icon="mingcute:close-line"
                  width="16px"
                  height="16px"
                />
              }
              onDelete={() => onFilterChange(item)}
            />
          ))}
        </Box>
        <ShowComponent condition={currentTab === "evals"}>
          <EvalCard
            setCurrentTab={setCurrentTabs}
            selectedColumns={appliedFilter
              ?.filter((e) => e.origin_type !== "evaluation")
              ?.map((item) => item.id)}
            datasetId={datasetId}
            selectedEvals={appliedFilter
              ?.filter((e) => e.origin_type === "evaluation")
              ?.map((e) => e?.id)}
            datasetIndex={datasetIndex}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "prompt"}>
          <PropmtCard
            setCurrentTab={setCurrentTabs}
            selectedColumns={appliedFilter?.map((item) => item.sourceId)}
            datasetId={datasetId}
            datasetIndex={datasetIndex}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "annotation"}>
          <AnnotationCard
            setCurrentTab={setCurrentTabs}
            selectedColumns={appliedFilter?.map((item) => item.sourceId)}
            datasetId={datasetId}
            datasetIndex={datasetIndex}
          />
        </ShowComponent>
      </Box>
    </Box>
  );
};

DatasetSummaryTab.propTypes = {
  setCurrentTabs: PropTypes.func,
  datasetId: PropTypes.string,
  datasetIndex: PropTypes.number,
};

export default DatasetSummaryTab;
