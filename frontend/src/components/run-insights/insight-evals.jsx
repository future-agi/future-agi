import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "../iconify";
import _ from "lodash";
import CustomChipRenderer from "./traces-tab/CustomRenderers/CustomChipRenderer";
import { ShowComponent } from "../show";
import FailedEvaluationsDialog from "./FailedEvaluationDialog";

const ERROR_MESSAGES = {
  RATE_LIMIT_MESSAGE:
    "Rate limit reached. Please try again later or upgrade your plan.",
  CREDIT_LIMIT_MESSAGE: "Insufficient credits. Please recharge your account.",
  SUPPORT_MESSAGE:
    "Required action could not be performed, please validate the input data. In case of query, please reach out to us at support@futureagi.com",
};

const filterMetrics = ["failedLatency", "failedCost"];

function getErrorMessageButton(result) {
  const evaluationWithButtons = {
    ...result,
    showRateLimitButton:
      result?.errorMessage === ERROR_MESSAGES.RATE_LIMIT_MESSAGE,
    showCreditLimitMessage:
      result?.errorMessage === ERROR_MESSAGES.CREDIT_LIMIT_MESSAGE,
    showReRunButton:
      ![
        ERROR_MESSAGES.RATE_LIMIT_MESSAGE,
        ERROR_MESSAGES.CREDIT_LIMIT_MESSAGE,
        ERROR_MESSAGES.SUPPORT_MESSAGE,
      ].includes(result?.errorMessage) && !!result?.errorMessage,
  };

  return evaluationWithButtons;
}

const InsightEvals = ({ evalMetrics }) => {
  const [openFailedModal, setOpenFailedModal] = useState(null);
  const evalsData = useMemo(
    () =>
      Object.entries(evalMetrics || {}).filter(
        ([key]) => !filterMetrics.includes(key),
      ),
    [evalMetrics],
  );

  const handleCloseModal = () => {
    setOpenFailedModal(null);
  };

  const handleErrorClick = useCallback((evaluation) => {
    if (!evaluation?.showIcon) return;
    const response = getErrorMessageButton(evaluation);
    setOpenFailedModal(response);
  }, []);

  return (
    <Box sx={{}}>
      <ShowComponent condition={evalsData.length > 0}>
        {evalsData.map(([key, metric]) => (
          <Box
            key={key}
            sx={{
              py: (theme) => theme.spacing(2),
              borderBottom: "1px solid",
              borderColor: "divider",
              display: "flex",
              flexDirection: "column",
              gap: (theme) => theme.spacing(1.5),
            }}
          >
            {/* Main Key */}
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Iconify
                icon="solar:check-circle-linear"
                width={20}
                color="status.success.fg"
              />
              <Typography
                ml={(theme) => theme.spacing(2)}
                fontWeight="fontWeightRegular"
                variant="s1"
                color="text.primary"
              >
                {metric.name}
              </Typography>
            </Box>

            {/* Sub-Keys and Values in Chips */}
            <Box
              sx={{
                display: "flex",
                gap: (theme) => theme.spacing(1.5),
                flexWrap: "wrap",
                marginLeft: (theme) => theme.spacing(5),
                marginBottom: (theme) => theme.spacing(1),
              }}
            >
              <CustomChipRenderer
                key={key}
                evalId={key}
                data={metric}
                type={metric?.evalType}
                handleErrorClick={handleErrorClick}
              />
            </Box>
          </Box>
        ))}
      </ShowComponent>
      <ShowComponent condition={!evalsData.length}>
        <Box
          sx={{
            display: "flex",
            position: "absolute",
            top: "65%",
            left: "25%",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography
            fontWeight="fontWeightRegular"
            color="text.primary"
            variant="s1"
            sx={{ marginLeft: (theme) => theme.spacing(1.5) }}
          >
            No evals have been applied
          </Typography>
        </Box>
      </ShowComponent>

      <FailedEvaluationsDialog
        open={Boolean(openFailedModal)}
        onClose={handleCloseModal}
        data={openFailedModal}
      />
    </Box>
  );
};

InsightEvals.propTypes = {
  evalMetrics: PropTypes.objectOf(
    PropTypes.shape({
      name: PropTypes.string,
      totalCount: PropTypes.number,
      avgFloatScore: PropTypes.number,
      avgBoolFailScore: PropTypes.number,
      avgBoolPassScore: PropTypes.number,
      strListScore: PropTypes.object,
      totalErrorsCount: PropTypes.number,
      failedTraceIds: PropTypes.array,
      evalType: PropTypes.string,
      errorMessage: PropTypes.string,
    }),
  ),
};

export default InsightEvals;
