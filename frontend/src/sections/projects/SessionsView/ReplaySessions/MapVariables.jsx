import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useParams } from "react-router-dom";
import { Box, Typography } from "@mui/material";
import { chatEvalColumns } from "src/components/run-tests/common";
import EvaluationMappingFormContent from "../../../common/EvaluationDrawer/EvaluationMappingFormContent";
import { useFormContext, useWatch } from "react-hook-form";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { FUTUREAGI_LLM_MODELS } from "../../../common/EvaluationDrawer/validation";
import { useEvaluationKeys } from "./useEvaluationKeys";
import Loading from "./Loading";

const MapVariables = ({ scenarioDetail }) => {
  const { control, formState } = useFormContext();
  const model = useWatch({
    control,
    name: "model",
  });
  const { observeId } = useParams();

  const {
    projectEvals,
    filteredRequiredKeys,
    groupedRequiredKeys,
    transformedOptionalKeys,
    isFutureagiBuilt,
    modelsToShow,
    isLoading,
  } = useEvaluationKeys(observeId, null, null, {
    alwaysFetch: true,
    alwaysCompute: true,
  });

  const { data: knowledgeBaseList } = useKnowledgeBaseList("", null);

  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const modelsToShowResolved =
    modelsToShow.length > 0 ? modelsToShow : FUTUREAGI_LLM_MODELS;

  const [showAll, setShowAll] = useState(false);

  const visibleItems = showAll ? projectEvals : projectEvals.slice(0, 10);

  const scenarioColumns = useMemo(() => {
    const columnConfig = scenarioDetail?.dataset_column_config;
    if (!columnConfig) return chatEvalColumns;
    const additionalCols = Object.entries(columnConfig).map(
      ([_id, config]) => ({
        field: config.name,
        headerName: config.name,
        dataType: config.type || "text",
      }),
    );
    const existingFields = new Set(chatEvalColumns.map((c) => c.field));
    const uniqueAdditional = additionalCols.filter(
      (c) => !existingFields.has(c.field),
    );
    return [...chatEvalColumns, ...uniqueAdditional];
  }, [scenarioDetail?.dataset_column_config]);

  const filteredColumns = useMemo(() => {
    if (isFutureagiBuilt && modelsToShow.length > 0) {
      return model === "" ? [] : scenarioColumns;
    }
    return scenarioColumns;
  }, [isFutureagiBuilt, modelsToShow, model, scenarioColumns]);

  if (isLoading) {
    return <Loading />;
  }

  if (projectEvals.length === 0 && !isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
          Nothing to map. Proceed to run the simulation.
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        background: "background.paper",
        width: "100%",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: 2,
      }}
    >
      <EvaluationMappingFormContent
        _module={"replay-sessions"}
        control={control}
        members={projectEvals}
        filteredRequiredKeys={filteredRequiredKeys}
        filteredColumns={filteredColumns}
        showTest={false}
        onTest={() => {}}
        formState={formState}
        selectedEval={{
          isGroupEvals: true,
        }}
        hideBackButtons={true}
        filteredVisibleItems={visibleItems}
        setShowAll={setShowAll}
        isFutureagiBuilt={isFutureagiBuilt}
        alwaysShowModel
        modelsToShow={modelsToShowResolved}
        groupedRequiredKeys={groupedRequiredKeys}
        transformedOptionalKeys={transformedOptionalKeys}
        showAll={showAll}
        hideGroupHeader={true}
        hideAddGroupButton={true}
        knowledgeBaseOptions={knowledgeBaseOptions}
      />
    </Box>
  );
};

MapVariables.propTypes = {
  scenarioDetail: PropTypes.object,
};

export default MapVariables;
