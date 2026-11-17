import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Tab, Tabs } from "@mui/material";
import { useParams } from "react-router";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";
import { CLASSIC_EXPERIMENT_COPY as COPY } from "src/launch/classicExperimentCopy";
import EvalsCard from "./EvalsCard/EvalsCard";
import PromptCard from "./PromptCard";
import FilterSummary from "./FilterSummary";
import { useEvalStats } from "./summaryApi";

// v1.56.1 Evals / Prompt summary, with the existing column filters. A separate
// host prevents verdict, review, split and version queries from mounting.
export default function ClassicDatasetSummary({
  setCurrentTabs,
  datasetId,
  datasetIndex,
}) {
  const { dataset } = useParams();
  const id = datasetId || dataset;
  const [tab, setTab] = useState("evals");
  const [selected, setSelected] = useState([]);
  const [applied, setApplied] = useState([]);
  const { data } = useGetDatasetDetail(id, {}, { column_config_only: true });
  const { data: evals = [] } = useEvalStats(id);
  const columns = useMemo(() => {
    const ordinary = (data?.column_config ?? []).filter((c) =>
      tab === "prompt"
        ? c.origin_type === "run_prompt"
        : ![
            "evaluation",
            "optimisation",
            "optimisation_evaluation",
            "evaluation_reason",
          ].includes(c.origin_type),
    );
    return tab === "prompt"
      ? ordinary
      : [
          ...ordinary,
          ...evals.map((e) => ({ ...e, origin_type: "evaluation" })),
        ];
  }, [data, evals, tab]);
  return (
    <Box sx={{ p: 1.5, flex: 1, overflowY: "auto" }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", mb: 2 }}>
        <Tabs
          value={tab}
          onChange={(_, value) => {
            setTab(value);
            setSelected([]);
            setApplied([]);
          }}
        >
          <Tab value="evals" label={COPY.evalsTab} />
          <Tab value="prompt" label={COPY.promptTab} />
        </Tabs>
        <FilterSummary
          columnLists={columns}
          selectedColumn={selected}
          setSelectedColumn={setSelected}
          appliedFilter={applied}
          handleApplyFilter={() => setApplied([...selected])}
        />
      </Box>
      {tab === "evals" ? (
        <EvalsCard
          classic
          setCurrentTab={setCurrentTabs}
          datasetId={id}
          datasetIndex={datasetIndex}
          selectedColumns={applied
            .filter((c) => c.origin_type !== "evaluation")
            .map((c) => c.id)}
          selectedEvals={applied
            .filter((c) => c.origin_type === "evaluation")
            .map((c) => c.id)}
        />
      ) : (
        <PromptCard
          setCurrentTab={setCurrentTabs}
          datasetId={id}
          datasetIndex={datasetIndex}
          selectedColumns={applied.map((c) => c.source_id)}
        />
      )}
    </Box>
  );
}
ClassicDatasetSummary.propTypes = {
  setCurrentTabs: PropTypes.func,
  datasetId: PropTypes.string,
  datasetIndex: PropTypes.number,
};
