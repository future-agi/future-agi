import { LoadingButton } from "@mui/lab";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import AddedEvaluationCard from "../Common/AddEvaluation/AddedEvaluationCard";
import { camelCaseToTitleCase } from "src/utils/utils";

const PromptEvaluationListPage = ({
  evalsConfigs,
  setEvalsConfigs,
  handleLabelsAdd,
  onClose,
}) => {
  return (
    <>
      <Box
        sx={{
          paddingX: "20px",
          flex: 1,
          gap: "10px",
          flexDirection: "column",
          display: "flex",
          overflowY: "auto",
        }}
      >
        <Box
          sx={{
            gap: "10px",
            flexDirection: "column",
            display: "flex",
            position: "sticky",
            top: 0,
            zIndex: 1,
            backgroundColor: "background.paper",
          }}
        >
          <Typography fontWeight={600} color="text.secondary">
            Added Evaluations
          </Typography>
        </Box>
        <Box
          sx={{
            gap: "10px",
            flexDirection: "column",
            display: "flex",
            flex: 1,
          }}
        >
          {evalsConfigs?.map((eachUserEval) => (
            <AddedEvaluationCard
              key={eachUserEval.id}
              userEval={null}
              requiredKeys={Object.keys(eachUserEval?.mapping || {})
                .map((k) => camelCaseToTitleCase(k))
                .join(", ")}
              model={""}
              name={eachUserEval?.name}
              description={eachUserEval?.description}
              viewConfig={{ checkbox: false, run: false }}
              onRemove={() => {
                if (handleLabelsAdd) handleLabelsAdd(null);
                setEvalsConfigs(
                  evalsConfigs.filter((e) => e.id !== eachUserEval.id),
                );
              }}
            />
          ))}
        </Box>
      </Box>
      <Box
        sx={{
          width: "100%",
          position: "sticky",
          bottom: "0px",
          paddingX: "20px",
          paddingY: "20px",
        }}
      >
        <LoadingButton
          variant="contained"
          color="primary"
          fullWidth
          disabled={!evalsConfigs?.length}
          //@ts-ignore
          onClick={() => {
            onClose();
            handleLabelsAdd("eval");
          }}
        >
          Add all evaluations
        </LoadingButton>
      </Box>
    </>
  );
};

PromptEvaluationListPage.propTypes = {
  evalsConfigs: PropTypes.array,
  setEvalsConfigs: PropTypes.func,
  handleLabelsAdd: PropTypes.func,
  onClose: PropTypes.func,
};

export default PromptEvaluationListPage;
