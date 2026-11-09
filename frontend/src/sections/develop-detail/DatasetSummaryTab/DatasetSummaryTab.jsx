import { isSurfaceShown } from "src/launch/launchSwitch";
import ClassicDatasetSummary from "./ClassicDatasetSummary";
import React, { useMemo, useState } from "react";
import { Box, Button } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import { FAB_GUTTER_PB } from "src/sections/common/ux/useFabGutter";
import PropTypes from "prop-types";
import { CustomTab, CustomTabs, TabWrapper } from "./SummaryStyle";
import { ShowComponent } from "src/components/show";
import EvalCard from "./EvalsCard/EvalsCard";
import FilterSummary from "./FilterSummary";
import Iconify from "src/components/iconify";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";
import { useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import StyledChip from "./styledChip";
import PropmtCard from "./PromptCard";
import HumanReviewTab from "./HumanReviewTab";
import SplitSelect from "./SplitSelect";
import DatasetVerdictCard from "./DatasetVerdictCard";
import {
  flattenEvalStats,
  useAnnotationSummary,
  useEvalStats,
  useSummarySplits,
} from "./summaryApi";

const tabOptions = [
  { label: "Evals", value: "evals", disabled: false },
  { label: "Human review", value: "human", disabled: false },
  { label: "Prompt", value: "prompt", disabled: false },
];

const EnhancedDatasetSummaryTab = ({
  setCurrentTabs,
  datasetId,
  datasetIndex,
}) => {
  const { dataset } = useParams();
  // AS#48: sub-tab, split and filter live in the URL (`stab`, `ssplit`,
  // `sfilter`; `view` and `ui` are reserved app-wide).
  const [vs, setVs] = useViewUrlState({
    stab: "evals",
    ssplit: [],
    sfilter: [],
  });
  const currentTab = tabOptions.some((t) => t.value === vs.stab)
    ? vs.stab
    : "evals";
  const splitIds = vs.ssplit;
  const setSplitIds = (ids) => setVs({ ssplit: ids ?? [] });
  const [selectedColumn, setSelectedColumn] = useState([]);
  const [searchParams] = useSearchParams();
  // All numbers respect the dataset version in view (`?version=`).
  const version = searchParams.get("version") || null;
  const splits = useSummarySplits(dataset);
  const annotation = useAnnotationSummary(dataset, { splitIds, version });

  const { data } = useGetDatasetDetail(
    dataset,
    {},
    { column_config_only: true },
  );
  // IT-R5 G2 P4 (budget ≤ 18 controls): a sub-tab with nothing in it is
  // not offered (the URL still opens it); one tab left → no tab strip.
  const review = annotation.data?.review;
  const hasReview =
    !annotation.data ||
    (review?.reviewed_rows ?? 0) > 0 ||
    Boolean(review?.queue) ||
    (annotation.data?.judge_agreement?.length ?? 0) > 0;
  const hasPrompt =
    !data?.column_config ||
    data.column_config.some((c) => c.origin_type === "run_prompt");
  const shownTabs = tabOptions.filter(
    (t) =>
      t.value === currentTab ||
      t.value === "evals" ||
      (t.value === "human" && hasReview) ||
      (t.value === "prompt" && hasPrompt),
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
  const setAppliedFilter = (list) =>
    setVs({ sfilter: (list ?? []).map((i) => i.id).filter(Boolean) });
  const onFilterChange = (item) => {
    const pre = appliedFilter;
    const newData = pre.some((temp) => temp.id === item.id)
      ? pre.filter((temp) => temp.id !== item.id)
      : [...pre, item];
    setSelectedColumn(
      (allListItem ?? []).filter((i) => newData.some((t) => t.id === i.id)),
    );
    setAppliedFilter(newData);
  };

  const handleApplyFilter = () => {
    setAppliedFilter([...selectedColumn]);
  };

  const changeTab = (e, value) => {
    setSelectedColumn([]);
    setVs({ stab: value, sfilter: [] });
  };

  const { data: evalsSummary } = useEvalStats(dataset);
  const evalOrder = useMemo(
    () => flattenEvalStats(evalsSummary).map((r) => r.id),
    [evalsSummary],
  );

  // IT-R5 G2 P4: no evals of its own yet (the Evals tab shows the empty
  // state and the borrowed experiment table instead).
  const noEvalsYet =
    currentTab === "evals" &&
    Array.isArray(evalsSummary) &&
    evalsSummary.length === 0;

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
  const appliedFilter = useMemo(
    () =>
      (vs.sfilter ?? [])
        .map((id) => columnLists.find((c) => c.id === id))
        .filter(Boolean),
    [vs.sfilter, columnLists],
  );
  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        padding: "12px",
      }}
    >
      {/* Same right gutter as the cards below, so the toolbar and the cards
          share one right edge (the Falcon bubble's gutter). */}
      <Box
        display="flex"
        justifyContent={"space-between"}
        sx={{ pr: FAB_GUTTER_PB }}
      >
        <TabWrapper sx={shownTabs.length < 2 ? { display: "none" } : undefined}>
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
            {shownTabs.map((tab) => (
              <CustomTab
                key={tab.value}
                label={tab.label}
                value={tab.value}
                disabled={tab.disabled}
              />
            ))}
          </CustomTabs>
        </TabWrapper>

        {/* IT-R5 G2 P4: Split and Filter only once there is data. */}
        <Box
          display={noEvalsYet ? "none" : "flex"}
          gap={1}
          alignItems="flex-start"
        >
          <SplitSelect
            splits={splits}
            value={splitIds}
            onChange={setSplitIds}
          />
          {currentTab === "human" && (
            <CustomTooltip
              show
              size="small"
              placement="top"
              title="Filter applies to the Evals tab only. Narrow human review with Split."
            >
              <span>
                <Button
                  size="small"
                  variant="outlined"
                  color="inherit"
                  disabled
                  startIcon={<Iconify icon="mdi:filter-outline" width={16} />}
                >
                  Filter
                </Button>
              </span>
            </CustomTooltip>
          )}
          {currentTab !== "human" && (
            <FilterSummary
              columnLists={columnLists}
              handleApplyFilter={handleApplyFilter}
              selectedColumn={selectedColumn}
              setSelectedColumn={setSelectedColumn}
              appliedFilter={appliedFilter}
            />
          )}
        </Box>
      </Box>
      <Box
        sx={{
          overflow: "auto",
          height: "calc(100vh - 200px)",
          // The Falcon bubble never covers a number (bottom + right gutter).
          pb: FAB_GUTTER_PB,
          pr: FAB_GUTTER_PB,
        }}
      >
        {/* IT-R5 G2 P4: the dataset answers first. */}
        <DatasetVerdictCard datasetId={datasetId || dataset} />
        <Box
          display={appliedFilter.length ? "flex" : "none"}
          gap={2}
          marginBottom={2}
        >
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
            splitIds={splitIds}
            version={version}
            agreement={annotation.data?.judge_agreement}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "human"}>
          <HumanReviewTab
            datasetId={datasetId || dataset}
            summary={annotation.data}
            isLoading={annotation.isLoading}
            version={version}
            evalOrder={evalOrder}
            setCurrentTab={setCurrentTabs}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "prompt"}>
          <PropmtCard
            setCurrentTab={setCurrentTabs}
            selectedColumns={appliedFilter?.map((item) => item.source_id)}
            datasetId={datasetId}
            datasetIndex={datasetIndex}
          />
        </ShowComponent>
      </Box>
    </Box>
  );
};

EnhancedDatasetSummaryTab.propTypes = {
  setCurrentTabs: PropTypes.func,
  datasetId: PropTypes.string,
  datasetIndex: PropTypes.number,
};

export default function DatasetSummaryTab(props) {
  return isSurfaceShown("dataset.tab.summary") ? (
    <EnhancedDatasetSummaryTab {...props} />
  ) : (
    <ClassicDatasetSummary {...props} />
  );
}
