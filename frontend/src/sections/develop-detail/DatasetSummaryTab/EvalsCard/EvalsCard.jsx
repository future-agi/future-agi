import React, { useMemo } from "react";
import { useParams } from "react-router";
import PropTypes from "prop-types";
import { flattenEvalStats, useEvalStats } from "../summaryApi";
import EmptyCard from "../EmptyCard";
import { useRunEvaluationStoreShallow } from "../../states";
import EvalsCardGraphs from "./EvalsCardGraphs";
import ExperimentEvalsCard from "./ExperimentEvalsCard";

const EvalsCard = ({
  setCurrentTab,
  datasetId = null,
  selectedColumns,
  datasetIndex,
  selectedEvals = [],
  splitIds,
  version,
  agreement,
  classic = false,
}) => {
  const { dataset } = useParams();
  const setOpenRunEvaluation = useRunEvaluationStoreShallow(
    (s) => s.setOpenRunEvaluation,
  );

  const activeDatasetId = datasetId || dataset;

  const { data, isPending, isLoading } = useEvalStats(activeDatasetId, {
    columnIds: selectedColumns,
    splitIds,
    version,
  });

  const formattedData = useMemo(() => {
    return selectedEvals?.length > 0
      ? data?.map((e) => ({
          ...e,
          isVisible: selectedEvals.includes(e?.id),
        }))
      : data?.map((e) => ({ ...e, isVisible: true }));
  }, [data, selectedEvals]);

  const overviewRows = useMemo(
    () => flattenEvalStats(formattedData?.filter((e) => e.isVisible)),
    [formattedData],
  );

  const empty = (
    <EmptyCard
      tab="evaluations"
      setCurrentTab={setCurrentTab}
      action={() => setOpenRunEvaluation(true)}
      datasetId={datasetId}
      icon="/assets/icons/summary/empty-evals.svg"
      title="No evaluations added"
      description="To see the summary and evaluate your dataset, add evals to the dataset"
    />
  );
  return (
    <EvalsCardGraphs
      emptyComponent={
        classic ? (
          empty
        ) : (
          <ExperimentEvalsCard
            datasetId={activeDatasetId}
            onAddEvals={() => setOpenRunEvaluation(true)}
            fallback={empty}
          />
        )
      }
      data={formattedData}
      isPending={isPending}
      isLoading={isLoading}
      datasetIndex={datasetIndex}
      overviewRows={classic ? undefined : overviewRows}
      agreement={agreement}
      datasetId={activeDatasetId}
      version={version}
    />
  );
};

EvalsCard.propTypes = {
  classic: PropTypes.bool,
  setCurrentTab: PropTypes.func,
  datasetId: PropTypes.string,
  selectedColumns: PropTypes.array,
  datasetIndex: PropTypes.number,
  selectedEvals: PropTypes?.array,
  splitIds: PropTypes.array,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  agreement: PropTypes.array,
};

export default EvalsCard;
