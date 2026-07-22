import React, { useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../AccordianElements";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useFieldArray, useWatch } from "react-hook-form";
import { Box } from "@mui/material";
import _ from "lodash";
import DeleteEval from "../Common/AddEvaluation/DeleteEval";
import { useParams } from "react-router";
import EvaluationSection from "src/sections/common/EvalsTasks/NewTaskDrawer/EvaluationSection";
import { useEvaluationContext } from "src/sections/common/EvaluationDrawer/context/EvaluationContext";

const AddedEvaluations = ({
  control,
  allColumns,
  columnFieldName = "columnId",
}) => {
  const selectedColumn = useWatch({ control, name: columnFieldName });
  const [hideDeleteColumn, setHideDeleteColumn] = useState(false);
  const { dataset } = useParams();
  const [open, setOpen] = useState(null);
  const { openEditForSavedEval } = useEvaluationContext();

  const fieldName = "userEvalTemplateIds";

  const { replace } = useFieldArray({
    control,
    name: fieldName,
  });

  const {
    data: userEvalList,
    refetch,
    isSuccess,
  } = useQuery({
    queryFn: () =>
      axios.get(endpoints.develop.optimizeDevelop.columnInfo, {
        params: { column_id: selectedColumn },
      }),
    queryKey: ["optimize-develop-column-info", selectedColumn],
    enabled: Boolean(selectedColumn?.length),
    select: (data) =>
      data?.data?.result?.map((item) => ({
        ...item,
        evalRequiredKeys: item?.templateDetails?.config?.requiredKeys || [],
      })),
  });

  useEffect(() => {
    if (isSuccess) {
      replace(
        userEvalList?.map((item) => ({ ...item, evalId: item.id })) || [],
      );
    }
  }, [replace, userEvalList, isSuccess]);

  useEffect(() => {
    const hide = !allColumns.some((item) => item?.col?.sourceId == open?.id);
    if (open) {
      setHideDeleteColumn(hide);
    } else {
      setTimeout(() => {
        setHideDeleteColumn(hide);
      }, 300);
    }
  }, [allColumns, open]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Accordion
        defaultExpanded
        sx={{
          display: "flex",
          flexDirection: "column",
        }}
      >
        <AccordionSummary>Evaluations</AccordionSummary>
        <AccordionDetails
          sx={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            flexGrow: 1,
            width: "100%",
            height: "100%",
          }}
        >
          <EvaluationSection
            selected={Boolean(selectedColumn?.length)}
            showRun={false}
            hideStatus
            savedEvals={userEvalList}
            isEvalsLoading={false}
            isProjectEvals={false}
            disabledMessage="Select a column to add evaluation"
            onRemoveEval={(item) => setOpen(item)}
            onEditEvalClick={openEditForSavedEval}
          />
          <DeleteEval
            open={Boolean(open)}
            setOpen={() => {
              // status?.isDeleted && remove(open?.idx);
              setOpen(null);
            }}
            dataset={dataset}
            refreshGrid={refetch}
            userEval={open}
            hideDeleteColumn={hideDeleteColumn}
          />
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};

AddedEvaluations.propTypes = {
  control: PropTypes.object,
  setEvaluationTypeOpen: PropTypes.func,
  setConfigureEvalOpen: PropTypes.func,
  setSelectedEval: PropTypes.func,
  allColumns: PropTypes.array,
  columnFieldName: PropTypes.string,
};

export default AddedEvaluations;
