import { Box, Button, Chip, Skeleton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import {
  buttonSx,
  OutputTypes,
} from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import AddToFeedBackModal, {
  SubmitFeedBackModal,
} from "../DrawerRightRenderer/AddToFeedBackModal";
import { ShowComponent } from "src/components/show/ShowComponent";
import CellMarkdown from "src/sections/common/CellMarkdown";
import NumericCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import EvalStatusIndicator from "src/components/eval/EvalStatusIndicator";
import { getEvalNonScoreStatus } from "src/utils/evalStatus";

const TooltipContent = ({
  explanation,
  onAddFeedback,
  onViewDetail,
  showViewDetail = true,
  showAddFeedback = true,
}) => {
  const [isAddFeedBack, setIsAddFeedBack] = React.useState(false);
  const [openSubmitFeedback, setOpenSubmitFeedback] = React.useState(false);
  const [expanded, setExpanded] = useState(false);
  const showMoreCondition =
    !expanded && explanation && explanation.length > 150;
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
      }}
    >
      <Box sx={{ overflowY: "auto", maxHeight: "40vh" }}>
        {explanation ? (
          <CellMarkdown
            spacing={0}
            text={
              explanation.length > 150 && !expanded
                ? `${explanation.slice(0, 150)}...`
                : explanation
            }
          />
        ) : (
          "No explanation provided"
        )}
      </Box>

      <Box>
        <ShowComponent condition={showMoreCondition}>
          <Box sx={{ mb: 1 }}>
            <Typography
              typography="s3"
              sx={{
                fontWeight: "fontWeightSemiBold",
                textDecoration: "underline",
                width: "20%",
                ":hover": {
                  cursor: "pointer",
                },
              }}
              onClick={() => setExpanded(true)}
            >
              Show More
            </Typography>
          </Box>
        </ShowComponent>
        <Box sx={{ display: "flex", width: "100%", gap: 1 }}>
          <ShowComponent condition={showViewDetail}>
            <Button
              size="small"
              fullWidth
              variant="outlined"
              startIcon={<Iconify icon="tabler:eye" />}
              sx={buttonSx}
              onClick={onViewDetail}
            >
              View Detail
            </Button>
          </ShowComponent>
          <ShowComponent condition={showAddFeedback}>
            <Button
              size="small"
              fullWidth
              variant="outlined"
              startIcon={<Iconify icon="tabler:message" />}
              onClick={onAddFeedback}
              sx={buttonSx}
            >
              Add Feedback
            </Button>
          </ShowComponent>
        </Box>
        <AddToFeedBackModal
          open={isAddFeedBack}
          handleClose={() => setIsAddFeedBack(false)}
          setOpenSubmitFeedback={setOpenSubmitFeedback}
        />
        <SubmitFeedBackModal
          openSubmitFeedback={openSubmitFeedback}
          handleSubmitFeedBackClose={() => setOpenSubmitFeedback(false)}
        />
      </Box>
    </Box>
  );
};

TooltipContent.propTypes = {
  explanation: PropTypes.string,
  onAddFeedback: PropTypes.func,
  onViewDetail: PropTypes.func,
  showViewDetail: PropTypes.bool,
  showAddFeedback: PropTypes.bool,
};

const EvalTooltip = ({
  children,
  onAddFeedback,
  onViewDetail,
  explanation,
  showViewDetail,
  showAddFeedback,
}) => {
  return (
    <CustomTooltip
      show
      title={
        <TooltipContent
          explanation={explanation}
          onAddFeedback={onAddFeedback}
          onViewDetail={onViewDetail}
          showViewDetail={showViewDetail}
          showAddFeedback={showAddFeedback}
        />
      }
      enterDelay={500}
      enterNextDelay={500}
      arrow
      expandable
      sx={{
        "& .MuiTooltip-tooltip": {
          maxWidth: "350px",
          minWidth: "336px",
        },
      }}
      slotProps={{
        popper: {
          modifiers: [
            {
              name: "offset",
              options: {
                offset: [0, -30],
              },
            },
          ],
        },
      }}
    >
      {children}
    </CustomTooltip>
  );
};

EvalTooltip.propTypes = {
  children: PropTypes.node,
  onAddFeedback: PropTypes.func,
  onViewDetail: PropTypes.func,
  explanation: PropTypes.string,
  showViewDetail: PropTypes.bool,
  showAddFeedback: PropTypes.bool,
};

const EvalsCustomCellRenderer = (props) => {
  const column = props?.column?.colDef;
  const loading = props.data?.loading;
  const error = props.data?.error;
  const outputType = props.data?.outputType;
  // Lifecycle status (pending/running/skipped) from the eval read APIs.
  const nonScoreStatus = getEvalNonScoreStatus(props.data?.status);
  const skippedReason = props.data?.skippedReason;
  const showAddFeedback = props?.showAddFeedback;
  const showViewDetail = props?.showViewDetail;

  const renderEval = () => {
    // if (outputType === "str_list") {
    //   return (
    //     <Box sx={{ display: "flex", gap: 1, padding: 1 }}>
    //       {props.value.map((each) => (
    //         <Chip key={each} label={each} variant="outlined" color="primary" />
    //       ))}
    //     </Box>
    //   );
    // }
    if (outputType === "str_list" || outputType === "array") {
      if (!Array.isArray(props.value)) {
        return (
          <Box sx={{ padding: 1 }}>
            <Typography color="error">-</Typography>
          </Box>
        );
      }
      return (
        <Box sx={{ display: "flex", gap: 1, padding: 1, width: "100%" }}>
          {props.value.map((each) => (
            <Chip key={each} label={each} variant="outlined" color="primary" />
          ))}
        </Box>
      );
    } else if (outputType === "bool" || outputType === "Pass/Fail") {
      const isPass =
        outputType === "Pass/Fail" ? props.value >= 50 : props.value === 100;
      return (
        <Box
          sx={{
            backgroundColor: interpolateColorBasedOnScore(
              isPass ? 100 : 0,
              100,
            ),
            width: "100%",
            padding: 1,
            lineHeight: "1.5",
            height: "100%",
            display: "flex",
            alignItems: "center",
            color: "text.secondary",
            typography: "s1",
          }}
        >
          {isPass ? "Passed" : "Failed"}
        </Box>
      );
    } else if (outputType === OutputTypes.NUMERIC) {
      return <NumericCell value={props.value} sx={{ padding: 1 }} />;
    } else {
      return (
        <Box
          sx={{
            backgroundColor: interpolateColorBasedOnScore(props.value, 100),
            width: "100%",
            padding: 1,
            lineHeight: "1.5",
            height: "100%",
            display: "flex",
            alignItems: "center",
          }}
        >
          <Typography typography="s1" sx={{ color: "text.secondary" }}>
            {props.value}%
          </Typography>
        </Box>
      );
    }
  };

  const renderContent = () => {
    // This is temporary change to show "-" when there is no value

    if (column.field === "score") {
      if (nonScoreStatus) {
        return (
          <Box sx={{ width: "100%", height: "100%" }}>
            <EvalStatusIndicator
              status={nonScoreStatus}
              skippedReason={skippedReason}
            />
          </Box>
        );
      }

      if (loading) {
        return (
          <Skeleton
            variant="rectangular"
            animation="wave"
            sx={{
              width: "100%",
              height: "100%",
              minHeight: 20,
              borderRadius: 0,
              transform: "none",
            }}
          />
        );
      }

      if (error) {
        return (
          <EvalTooltip
            explanation={props.data?.explanation}
            onAddFeedback={() => {
              props.setSelectedAddFeedback(props.data);
            }}
            showViewDetail={false}
            showAddFeedback={false}
          >
            <Box
              sx={{
                color: "error.main",
                opacity: 1,
                flex: 1,
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
                width: "100%",
              }}
            >
              <Typography variant="body2" align="center">
                Error
              </Typography>
            </Box>
          </EvalTooltip>
        );
      }

      if (props.value === null || props.value === undefined) {
        return (
          <Box
            sx={{
              padding: 1,
              lineHeight: "1.5",
            }}
          >
            -
          </Box>
        );
      }
      return (
        <EvalTooltip
          explanation={props.data?.explanation}
          showAddFeedback={!!showAddFeedback}
          showViewDetail={!!showViewDetail}
          onAddFeedback={() => {
            props.setSelectedAddFeedback(props.data);
          }}
          onViewDetail={() => {
            props.setSelectedViewDetail(props.data);
          }}
        >
          {renderEval()}
        </EvalTooltip>
      );
    }

    return (
      <Box
        sx={{
          whiteSpace: "pre-wrap",
          lineHeight: "1.5",
          overflow: "hidden",
          textOverflow: "ellipsis",
          display: "-webkit-box",
          WebkitLineClamp: "4",
          WebkitBoxOrient: "vertical",
          padding: 1,
          color: "text.primary",
          typography: "s1",
          fontWeight: "fontWeightRegular",
        }}
      >
        {props.value}
      </Box>
    );
  };

  return (
    <Box sx={{ flex: 1, display: "flex", alignItems: "center" }}>
      {renderContent()}
    </Box>
  );
};

EvalsCustomCellRenderer.propTypes = {
  column: PropTypes.object,
  value: PropTypes.string,
  data: PropTypes.object,
  setSelectedAddFeedback: PropTypes.func,
  setSelectedViewDetail: PropTypes.func,
  showAddFeedback: PropTypes.bool,
  showViewDetail: PropTypes.bool,
};

export default EvalsCustomCellRenderer;
