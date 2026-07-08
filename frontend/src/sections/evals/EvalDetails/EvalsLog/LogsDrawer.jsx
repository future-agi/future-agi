import React, { useMemo, useState } from "react";
import {
  Box,
  Divider,
  Drawer,
  IconButton,
  Skeleton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { copyToClipboard } from "src/utils/utils";
import DatapointCard from "src/sections/common/DatapointCard";
import LogDrawerRight from "./LogDrawerRight";
import { enqueueSnackbar } from "notistack";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { format } from "date-fns";
import AudioDatapointCard from "src/components/custom-audio/AudioDatapointCard";
import ImageDatapointCard from "src/sections/common/ImageDatapointCard";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import AddEvalsFeedbackDrawer from "./../EvalsFeedback/AddEvalsFeedbackDrawer";

const LogsDrawerChild = ({
  onClose,
  data,
  evalsId,
  refreshGrid,
  evalOutputTypes,
}) => {
  const [openAddFeedback, setOpenAddFeedback] = useState(false);
  const isNumericOutput = useMemo(
    () => Object.values(evalOutputTypes || {}).includes("numeric"),
    [evalOutputTypes],
  );

  const addFeedbackClick = () => {
    setOpenAddFeedback(true);
  };

  const topMemu = useMemo(() => {
    trackEvent(Events.usageLogIdClicked, {
      [PropertyName.evalId]: evalsId,
      [PropertyName.source]: data?.source,
    });
    return [
      {
        key: "Evaluation Id",
        value: data.evaluation_id,
        evaluationId: data.evaluation_id,
        showCopy: true,
      },
      {
        key: "Experiment Id",
        value: data.experiment_id,
        experimentId: data.experiment_id,
        showCopy: true,
      },
      {
        key: "Prompt Template Id",
        value: data.prompt_template_id,
        promptTemplateId: data.prompt_template_id,
        showCopy: true,
      },
      {
        key: "Prompt Version Id",
        value: data.prompt_version_id,
        promptVersionId: data.prompt_version_id,
        showCopy: true,
      },
      {
        key: "Dataset Id",
        value: data.dataset_id,
        datasetId: data.dataset_id,
        showCopy: true,
      },
      {
        key: "Trace Id",
        value: data.trace_id,
        traceId: data.trace_id,
        showCopy: true,
      },
      {
        key: "Span Id",
        value: data.span_id,
        spanId: data.span_id,
        showCopy: true,
      },
      {
        key: "Source",
        value: data.source,
        source: data.source,
        showCopy: true,
      },
      {
        key: "Date",
        value: data.created_at
          ? format(new Date(data.created_at), "yyyy-MM-dd")
          : "",
        showCopy: false,
      },
    ];
  }, [data]);

  const closeAddFeedback = (submitted) => {
    setOpenAddFeedback(false);
    if (submitted) {
      onClose();
    }
  };

  return (
    <Box
      display={"flex"}
      flexDirection={"column"}
      gap="16px"
      padding={"16px"}
      height="100%"
    >
      {/* Header */}
      <Box display={"flex"} flexDirection={"column"} gap="12px">
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography
            typography="m3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Evaluation Logs
          </Typography>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="akar-icons:cross" sx={{ color: "text.primary" }} />
          </IconButton>
        </Box>
        <Box display={"flex"} gap="8px" flexWrap={"wrap"}>
          {topMemu.map((item, ind) => {
            const copyItem = item?.value;
            if (!copyItem) return null;
            return (
              <Box
                key={ind}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  display: "flex",
                  gap: "8px",
                  alignItems: "center",
                  borderRadius: "8px",
                  padding: "2px 8px",
                }}
              >
                <Typography
                  variant="s2"
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  {item?.key}: {copyItem}
                </Typography>
                {item?.showCopy && (
                  <SvgColor
                    src="/assets/icons/ic_copy.svg"
                    alt="Copy"
                    sx={{
                      width: "12px",
                      height: "12px",
                      color: "text.disabled",
                      cursor: "pointer",
                    }}
                    onClick={() => {
                      copyToClipboard(copyItem);
                      enqueueSnackbar("Copied to clipboard", {
                        variant: "success",
                      });
                    }}
                  />
                )}
              </Box>
            );
          })}
        </Box>
        <Divider orientation="horizontal" />
      </Box>
      <Box display={"flex"} gap="16px" height="calc(100% - 80px)">
        <Box sx={{ width: "100%" }}>
          <LogDrawerRight
            output={data?.output}
            error={data?.error_details || {}}
            addFeedbackClick={addFeedbackClick}
            isNumericOutput={isNumericOutput}
          />
        </Box>
        <Divider orientation="vertical" />
        <Box
          sx={{
            width: "100%",
            display: "flex",
            flexDirection: "column",
            gap: 2,
            overflowY: "auto",
          }}
        >
          {data?.required_keys?.map((item, index) => {
            const value = { cellValue: data?.values?.[item] || "-" };
            const columm = {
              headerName: item,
              dataType: data?.input_data_types?.[item],
            };
            const isAudioColumn = columm["dataType"] === "audio";
            const isImageColumn = columm["dataType"] === "image";
            if (isAudioColumn) {
              return (
                <AudioPlaybackProvider key={index}>
                  <AudioDatapointCard value={value} column={columm} />
                </AudioPlaybackProvider>
              );
            } else if (isImageColumn) {
              return (
                <ImageDatapointCard key={index} value={value} column={columm} />
              );
            }

            return (
              <DatapointCard
                key={index}
                value={value}
                column={columm}
                allowCopy={true}
                sx={{
                  backgroundColor: "background.default",
                  borderTop: "1px solid",
                  borderColor: "divider",
                  borderBottomLeftRadius: "8px",
                  borderBottomRightRadius: "8px",
                }}
              />
            );
          })}
        </Box>
      </Box>

      <AddEvalsFeedbackDrawer
        open={openAddFeedback}
        onClose={closeAddFeedback}
        output={data?.output || {}}
        evalsId={evalsId}
        selectedAddFeedback={{
          id: data.evaluation_id,
          ...(data?.output || {}),
        }}
        refreshGrid={refreshGrid}
      />
    </Box>
  );
};

LogsDrawerChild.propTypes = {
  selectedRow: PropTypes.object,
  onClose: PropTypes.func,
  data: PropTypes.object,
  evalsId: PropTypes.string,
  refreshGrid: PropTypes.func,
  evalOutputTypes: PropTypes.object,
};

const LogsDrawer = ({
  open,
  selectedRow,
  onClose,
  evalsId,
  refreshGrid,
  evalOutputTypes,
}) => {
  const { data, isPending, isRefetching, isLoading, isFetching } = useQuery({
    queryKey: ["evalslogsData", selectedRow?.logId],
    queryFn: async () =>
      axios.get(endpoints.develop.eval.getEvalLogs, {
        params: {
          log_id: selectedRow.logId,
          order: selectedRow.order,
          source: "logs",
        },
      }),
    enabled: !!selectedRow?.logId,
    staleTime: 1000,
  });

  return (
    <Drawer
      anchor="right"
      open={open}
      variant="temporary"
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "70%",
          position: "fixed",
          zIndex: 10,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      {isPending || isRefetching || isLoading || isFetching ? (
        <LoaderDrawer onClose={onClose} />
      ) : (
        <LogsDrawerChild
          selectedRow={selectedRow}
          onClose={onClose}
          data={data?.data?.result}
          evalsId={evalsId}
          refreshGrid={refreshGrid}
          evalOutputTypes={evalOutputTypes}
        />
      )}
    </Drawer>
  );
};

export default LogsDrawer;

LogsDrawer.propTypes = {
  open: PropTypes.bool,
  selectedRow: PropTypes.object,
  onClose: PropTypes.func,
  evalsId: PropTypes.string,
  refreshGrid: PropTypes.func,
  evalOutputTypes: PropTypes.object,
};

const LoaderDrawer = ({ onClose }) => {
  return (
    <Box
      display={"flex"}
      flexDirection={"column"}
      gap="16px"
      padding={"16px"}
      height="100%"
    >
      {/* Header */}
      <Box display={"flex"} flexDirection={"column"} gap="12px">
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Skeleton
            height={20}
            width={250}
            variant="rectangular"
            sx={{ borderRadius: "4px" }}
          />
          <IconButton onClick={onClose} size="small">
            <Iconify icon="akar-icons:cross" sx={{ color: "text.primary" }} />
          </IconButton>
        </Box>
        <Box display={"flex"} gap="8px" flexWrap={"wrap"}>
          {[1, 2, 3, 4].map((_, ind) => {
            return (
              <Box key={ind}>
                <Skeleton
                  height={20}
                  width={200}
                  variant="rectangular"
                  sx={{ borderRadius: "4px" }}
                />
              </Box>
            );
          })}
        </Box>
        <Divider orientation="horizontal" />
      </Box>
      <Box display={"flex"} gap="16px" height="100%">
        <Box
          display={"flex"}
          gap="16px"
          flexDirection={"column"}
          sx={{ width: "100%" }}
        >
          {[1, 2, 3, 4].map((item, index) => {
            return (
              <Box
                key={index}
                display={"flex"}
                gap="4px"
                flexDirection={"column"}
              >
                <Skeleton
                  height={30}
                  variant="rectangular"
                  sx={{ borderRadius: "4px" }}
                />
                <Skeleton
                  height={100}
                  variant="rectangular"
                  sx={{ borderRadius: "4px" }}
                />
              </Box>
            );
          })}
        </Box>
        <Divider orientation="vertical" />
        <Box
          sx={{ width: "100%" }}
          display={"flex"}
          gap="12px"
          flexDirection={"column"}
        >
          <Skeleton
            height={20}
            width={200}
            variant="rectangular"
            sx={{ borderRadius: "4px" }}
          />
          <Skeleton
            height={150}
            variant="rectangular"
            sx={{ borderRadius: "4px" }}
          />
          <Skeleton
            height={100}
            variant="rectangular"
            sx={{ borderRadius: "4px" }}
          />
          <Skeleton
            height={180}
            variant="rectangular"
            sx={{ borderRadius: "4px" }}
          />
          <Skeleton
            height={100}
            variant="rectangular"
            sx={{ borderRadius: "4px" }}
          />
        </Box>
      </Box>
    </Box>
  );
};

LoaderDrawer.propTypes = {
  onClose: PropTypes.func,
};
