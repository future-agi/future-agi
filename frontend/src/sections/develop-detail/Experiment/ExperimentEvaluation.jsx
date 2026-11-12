import { Box, Divider, FormHelperText, Typography } from "@mui/material";
import React, { useEffect, useState } from "react";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import EvaluationPresetCard from "../Common/EvaluationPresetCard";
import AddedEvaluationCard from "../Common/AddEvaluation/AddedEvaluationCard";
import { camelCaseToTitleCase } from "src/utils/utils";
import { useFieldArray, useFormState, useWatch } from "react-hook-form";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import _ from "lodash";
import DeleteEval from "../Common/AddEvaluation/DeleteEval";
import { useParams } from "react-router";

const ExperimentEvaluation = ({
  control,
  setEvaluationTypeOpen,
  setConfigureEvalOpen,
  setSelectedEval,
}) => {
  const selectedColumn = useWatch({ control, name: "columnId" });
  const { dataset } = useParams();
  const [open, setOpen] = useState(null);

  const fieldName = "userEvalTemplateIds";

  const { fields, replace } = useFieldArray({
    control,
    name: fieldName,
  });

  const { errors } = useFormState({ control });

  const errorMessage = _.get(errors, `${fieldName}`)?.message;
  const isError = !!errorMessage;

  const { data: userEvalList, refetch } = useQuery({
    queryFn: () =>
      axios.get(endpoints.develop.optimizeDevelop.columnInfo, {
        params: { column_id: selectedColumn },
      }),
    queryKey: ["optimize-develop-column-info", selectedColumn],
    enabled: Boolean(selectedColumn?.length),
    select: (data) => data?.data?.result,
  });

  useEffect(() => {
    replace(userEvalList?.map((item) => ({ ...item, evalId: item.id })) || []);
  }, [replace, userEvalList]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          paddingX: 2,
        }}
      >
        <CustomTooltip
          show={!selectedColumn?.length}
          title="Select a column first"
          arrow
        >
          <EvaluationPresetCard
            onClick={() => {
              if (!selectedColumn?.length) return;
              setEvaluationTypeOpen(true);
              setConfigureEvalOpen(false);
              setSelectedEval(false);
            }}
            title="+ Create eval"
            subTitle="Select from our preset evaluation metrics or configure your own evaluation metrics"
          />
        </CustomTooltip>
        <CustomTooltip
          show={!selectedColumn?.length}
          title="Select a column first"
          arrow
        >
          <EvaluationPresetCard
            onClick={() => {
              if (!selectedColumn?.length) return;
              setEvaluationTypeOpen(false);
              setConfigureEvalOpen(true);
              setSelectedEval(false);
            }}
            title="+ Use Saved evals"
            subTitle="Choose from  the list of previously configured evaluation metrics"
          />
        </CustomTooltip>
      </Box>
      <Divider />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          paddingX: 2,
          paddingBottom: 2,
        }}
      >
        <Box sx={{ gap: "12px", display: "flex", flexDirection: "column" }}>
          <Typography fontWeight={600} color="text.secondary" fontSize="13px">
            Added Evaluations
          </Typography>
          <Typography variant="body2" fontSize="12px">
            {fields?.length} selected
          </Typography>
        </Box>
        {fields?.map((userEval, idx) => (
          <AddedEvaluationCard
            userEval={userEval}
            key={userEval.id}
            name={userEval?.name}
            description={userEval?.templateDetails?.description}
            requiredKeys={userEval?.templateDetails?.config?.requiredKeys
              ?.map((k) => camelCaseToTitleCase(k))
              .join(", ")}
            model={userEval?.config?.config?.model}
            viewConfig={{ checkbox: false, run: false }}
            onRemove={() => setOpen({ ...userEval, idx })}
          />
        ))}
        <DeleteEval
          open={Boolean(open)}
          setOpen={() => setOpen(null)}
          dataset={dataset}
          refreshGrid={refetch}
          userEval={open}
        />
        {isError && <FormHelperText error>{errorMessage}</FormHelperText>}
      </Box>
    </Box>
  );
};

ExperimentEvaluation.propTypes = {
  control: PropTypes.object,
  setEvaluationTypeOpen: PropTypes.func,
  setConfigureEvalOpen: PropTypes.func,
  setSelectedEval: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default ExperimentEvaluation;
