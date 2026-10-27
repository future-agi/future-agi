/* eslint-disable react/prop-types */
import {
  Box,
  Button,
  Chip,
  Divider,
  Link,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import { format } from "date-fns";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Image from "src/components/image";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import CallStatus from "src/sections/test/CallLogs/CallStatus";
import {
  KeyOptimizerMapping,
  mapConfigurationToForm,
} from "../CreateEditOptimization/common";
import RerunOptimizationModal from "../CreateEditOptimization/RerunOptimizationModal";
import OptimizeAgentHeaderComponentSkeleton from "./OptimizeAgentHeaderComponentSkeleton";
import { AgentPromptOptimizerRerunStatus } from "../FixMyAgentDrawer/common";
import { useNavigate, useParams } from "react-router";
import CustomTooltip from "src/components/tooltip";
import { getDocsLinkBasedOnOptimizer } from "./common";
import { formatStartTimeByRequiredFormat } from "src/utils/utils";

const OptimizeAgentHeaderComponent = ({ optimization, isLoading }) => {
  const {
    model,
    status,
    optimiser_type: optimiserType,
    optimiser_name: optimiserName,
    provider_logo: providerLogo,
    start_time: startTime,
  } = optimization || {};
  const [feedbackModalOpen, setFeedbackModalOpen] = useState(false);
  const [createEditOptimizationModalOpen, setCreateEditOptimizationModalOpen] =
    useState(null);
  const navigate = useNavigate();
  const { testId, executionId } = useParams();
  const [anchorRef, setAnchorRef] = useState(null);

  const { control } = useForm({
    defaultValues: { description: "" },
  });

  const getDefaultValues = (optimization) => {
    return {
      name: `${optimiserName} - Rerun - ${format(new Date(), "dd MMM yyyy")}`,
      model: optimization?.model,
      optimiserType: optimiserType,
      configuration: mapConfigurationToForm(optimization?.configuration),
    };
  };

  if (isLoading) {
    return <OptimizeAgentHeaderComponentSkeleton />;
  }
  const formattedStartTime = formatStartTimeByRequiredFormat(
    startTime,
    "MMM dd, yyyy 'at' h:mm a",
  );
  return (
    <Stack spacing={2}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Typography variant="m3" fontWeight={"fontWeightSemiBold"}>
            {optimiserName}
          </Typography>
          <CallStatus value={status ?? "Completed"} />
        </Box>

        <ShowComponent condition={!!startTime && !!formattedStartTime}>
          <Typography typography={"s3"} fontWeight={"fontWeightRegular"}>
            Optimization ran on {formattedStartTime}
          </Typography>
        </ShowComponent>
      </Box>

      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Chip
            sx={{
              backgroundColor: "action.hover",
              color: "primary.main",
              ":hover": {
                backgroundColor: "action.hover",
              },
              "& .MuiChip-icon": { color: "primary.main" },
            }}
            icon={
              <SvgColor
                sx={{ width: 16 }}
                src="/assets/icons/navbar/ic_optimize.svg"
              />
            }
            label={`Optimizer Used - ${KeyOptimizerMapping?.[optimiserType] ?? optimiserType}`}
          />

          <Chip
            sx={{
              backgroundColor: "transparent",
              color: "text.primary",
              borderRadius: 0.5,
              fontWeight: 600,
              border: "1px solid",
              borderColor: "divider",
              ":hover": {
                backgroundColor: "transparent",
                borderColor: "divider",
              },
              "& .MuiChip-icon": { color: "text.primary" },
            }}
            icon={
              <Image
                ratio="1/1"
                src={providerLogo}
                alt={model}
                style={{ width: "16px", height: "16px" }}
              />
            }
            label={model || "Model"}
          />
          <ShowComponent condition={optimization?.parameters?.length > 0}>
            <Button
              sx={{ px: "8px" }}
              variant="outlined"
              size="small"
              startIcon={
                <SvgColor
                  src="/assets/prompt/slider-options.svg"
                  sx={{ height: "16px", width: "16px", color: "text.primary" }}
                />
              }
              onClick={(e) => setAnchorRef(e.currentTarget)}
            >
              {`Parameters${optimization?.parameters?.length > 0 ? ` (${optimization?.parameters?.length})` : ""}`}
            </Button>
          </ShowComponent>
        </Box>

        {/* Right Section (only when completed) */}
        <ShowComponent
          condition={AgentPromptOptimizerRerunStatus.includes(status)}
        >
          <Box sx={{ display: "flex", gap: 1 }}>
            <Button
              variant="outlined"
              size="small"
              sx={{ borderColor: "divider" }}
              startIcon={
                <SvgColor src={"/assets/icons/navbar/ic_evaluate.svg"} />
              }
              onClick={() =>
                setCreateEditOptimizationModalOpen(
                  getDefaultValues(optimization),
                )
              }
            >
              Rerun Optimization
            </Button>

            {/* <Button
              variant="outlined"
              sx={{ borderColor: "text.disabled" }}
              onClick={() => setFeedbackModalOpen(true)}
              startIcon={<SvgColor src={"/assets/icons/ic_feedback.svg"} />}
            >
              Add Feedbacks
            </Button> */}
          </Box>
        </ShowComponent>
      </Box>
      <ModalWrapper
        actionBtnTitle="Submit Feedback"
        open={feedbackModalOpen}
        title="Optimization Feedback"
        subTitle="Help us improve by sharing your thoughts on the prompt optimization results."
        onCancelBtn={() => {
          setFeedbackModalOpen(false);
        }}
        onClose={() => {
          setFeedbackModalOpen(false);
        }}
      >
        <Box
          sx={{
            border: "1px solid",
            backgroundColor: "background.neutral",
            borderColor: "divider",
            padding: 2,
            borderRadius: 0.5,
          }}
        >
          <FormTextFieldV2
            control={control}
            fieldName="description"
            label="Description"
            placeholder="Please describe how we can improve our optimization. (eg: ‘The optimized prompt was too verbos’ or ‘It changed the tone in a way I didn’t want’)"
            multiline
            sx={{ backgroundColor: "background.paper" }}
            minRows={4}
            maxRows={10}
            inputProps={{
              style: {
                minHeight: "163px",
                overflowY: "auto",
                whiteSpace: "pre-wrap",
              },
            }}
            fullWidth
          />
        </Box>
      </ModalWrapper>
      <RerunOptimizationModal
        open={createEditOptimizationModalOpen}
        onClose={() => setCreateEditOptimizationModalOpen(null)}
        defaultValues={createEditOptimizationModalOpen}
        onSuccess={(data) => {
          navigate(
            `/dashboard/simulate/test/${testId}/${executionId}/${data?.data?.id}`,
          );
        }}
      />
      <Popover
        open={Boolean(anchorRef)}
        anchorEl={anchorRef}
        onClose={() => setAnchorRef(null)}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
      >
        <Box sx={{ p: 1, minWidth: 288 }}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Typography
              typography={"s1"}
              fontWeight={"fontWeightBold"}
              gutterBottom
            >
              Parameters
            </Typography>
            <Link
              href={getDocsLinkBasedOnOptimizer(optimiserType)}
              color="blue.500"
              target="_blank"
              rel="noopener noreferrer"
              fontWeight="fontWeightMedium"
              fontSize="14px"
              sx={{
                textDecoration: "underline",
                fontSize: "13px",
                alignSelf: "center",
                marginBottom: 1,
              }}
            >
              Learn more
            </Link>{" "}
          </Box>

          <ShowComponent condition={optimization?.parameters?.length > 0}>
            <Stack spacing={1}>
              {optimization?.parameters?.map((param) => {
                return (
                  <Stack key={param?.key} spacing={0.5}>
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "row",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <Box
                        sx={{
                          display: "flex",
                          flexDirection: "row",
                          alignItems: "center",
                          gap: 0.5,
                        }}
                      >
                        <Typography
                          typography={"s2_1"}
                          fontWeight={"fontWeightRegular"}
                        >
                          {`${param?.label}`}
                        </Typography>
                        <CustomTooltip
                          type="black"
                          show={!!param?.description}
                          title={param?.description}
                          placement="top"
                        >
                          <SvgColor
                            sx={{
                              width: "12px",
                              height: "12px",
                              marginTop: "4px",
                            }}
                            src="/assets/icons/ic_info.svg"
                          />
                        </CustomTooltip>
                      </Box>

                      <Typography
                        typography={"s2_1"}
                        fontWeight={"fontWeightMedium"}
                      >
                        {param?.value}
                      </Typography>
                    </Box>
                    <ShowComponent
                      condition={
                        optimization?.parameters?.indexOf(param) !==
                        optimization?.parameters?.length - 1
                      }
                    >
                      <Divider />
                    </ShowComponent>
                  </Stack>
                );
              })}
            </Stack>
          </ShowComponent>
        </Box>
      </Popover>
    </Stack>
  );
};

OptimizeAgentHeaderComponent.propTypes = {
  optimization: PropTypes.shape({
    id: PropTypes.string,
    // name: PropTypes.string, //jaya need to send
    agentOptimiser: PropTypes.string,
    agentOptimiserRun: PropTypes.string,
    optimiserType: PropTypes.string,
    model: PropTypes.string,
    status: PropTypes.string,
    result: PropTypes.any,
    startTime: PropTypes.string,
    parameters: PropTypes.array,
    optimiserName: PropTypes.string,
    configuration: PropTypes.object,
  }),
  isLoading: PropTypes.bool,
};

export default OptimizeAgentHeaderComponent;
