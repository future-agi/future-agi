import { Box, Drawer, Grid, Typography, useTheme } from "@mui/material";
import React, { useMemo, useState } from "react";

import { ShowComponent } from "../show";
import PropTypes from "prop-types";
import CallLogTranscript from "./CallLogTranscript";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";
import TestDetailDrawerScenarioTable from "src/sections/test-detail/TestDetailDrawer/TestDetailDrawerScenarioTable";
import { StereoMultiTrackPlayer } from "src/sections/test-detail/TestDetailDrawer/AudioPlayerCustom";
import TestDetailDrawerRightSection from "src/sections/test-detail/TestDetailDrawer/TestDetailDrawerRightSection";
import { useCallLogsSideDrawerStore } from "./store";
import CallLogsDetailDrawerHeaderSection from "./CallLogsDetailDrawerHeaderSection";
import TestDetailDrawerHeaderSection from "src/sections/test-detail/TestDetailDrawer/TestDetailDrawerHeaderSection";
import LeftSection from "./LeftSection";
import RightSection from "./RightSection";
import { normalizeRecordings } from "src/utils/utils";
import AnnotationSidebarContent from "src/components/traceDetailDrawer/AnnotationSidebarContent";
import AddLabelDrawer from "src/components/traceDetailDrawer/AddLabelDrawer";
import { useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { buildVoiceCallAnnotationSources } from "src/components/voiceAnnotationSources";
// import ErrorAnalysis from "src/sections/traceDetailDrawer/ErrorAnalysis";

const CallLogSideDrawerChild = ({ data }) => {
  const { currentLeftTab, setCurrentLeftTab } = useCallLogsSideDrawerStore();
  const [annotationSidebarOpen, setAnnotationSidebarOpen] = useState(false);
  const [addLabelDrawerOpen, setAddLabelDrawerOpen] = useState(false);
  const queryClient = useQueryClient();
  const { agentDefinitionId: urlAgentDefinitionId, observeId } = useParams();
  const resolvedProjectId = observeId || data?.projectId;
  const filteredTranscript = useMemo(() => {
    return data?.transcript?.filter((item) => item.speakerRole !== "system");
  }, [data]);
  const theme = useTheme();

  const { scenarioId } = useMemo(() => {
    let scenarioPath = null;
    if (!data) return { scenarioId: null, scenarioPath: null };
    const scenarioPathColumn = Object.values(data?.scenarioColumns || {})?.find(
      (item) => item.columnName === "scenario_flow",
    );
    try {
      scenarioPath = JSON.parse(scenarioPathColumn?.value).map(
        (item) => item.name,
      );
    } catch (error) {
      return { scenarioId: data?.scenarioId, scenarioPath: null };
    }
    return {
      scenarioId: data?.scenarioId,
      scenarioPath: scenarioPath,
    };
  }, [data]);

  const rootObsSpan =
    data?.observation_span?.find(
      (s) => !s?.parent_span_id && s?.observation_type === "conversation",
    ) ||
    data?.observation_span?.find((s) => !s?.parent_span_id) ||
    data?.observation_span?.[0];
  const traceId = data?.trace_id || data?.id;
  const annotationSources = useMemo(
    () =>
      buildVoiceCallAnnotationSources({
        traceId,
        rootSpanId: rootObsSpan?.id,
        module: data?.module,
        callExecutionId: data?.id,
      }),
    [traceId, rootObsSpan?.id, data?.module, data?.id],
  );

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        width: "90vw",
      }}
    >
      <ShowComponent condition={data?.module === "project"}>
        <CallLogsDetailDrawerHeaderSection
          data={data}
          onAnnotate={() => setAnnotationSidebarOpen(true)}
        />
        {/* <Grid
                    item
                    xs={12}
                    sx={{
                        px: 3,
                        py: 2,
                    }}
                > */}
        {/* <ErrorAnalysis
                    traceId={traceData?.trace_id}
                    traceDetail={traceDetail}
                /> */}
        {/* </Grid> */}
      </ShowComponent>
      <ShowComponent condition={data?.module === "simulate"}>
        <TestDetailDrawerHeaderSection data={data} />
        <TestDetailDrawerScenarioTable data={data} />
      </ShowComponent>
      <Grid container spacing={2} px={2} mb={2}>
        <Grid
          item
          xs={6}
          md={6}
          display="flex"
          flexDirection="column"
          gap={2}
          height="100%"
        >
          <ShowComponent condition={data?.module === "simulate"}>
            <Typography
              typography="s1"
              sx={{ my: 1 }}
              fontWeight="fontWeightSemiBold"
            >
              Recording
            </Typography>
            <Box
              border="1px solid"
              borderColor="divider"
              borderRadius={1}
              p={2}
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                {data?.audioUrl ? (
                  <StereoMultiTrackPlayer
                    recordings={normalizeRecordings(data?.recordings)}
                    id={data?.id}
                  />
                ) : (
                  <Box
                    justifyContent="center"
                    alignItems="center"
                    display="flex"
                    flex={1}
                    minHeight={200}
                  >
                    <Typography typography="s2_1">
                      No recording found - <i>{data?.endedReason}</i>
                    </Typography>
                  </Box>
                )}
              </Box>
            </Box>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 0.5,
                height: "100%",
              }}
            >
              <TabWrapper sx={{ height: "38px" }}>
                <CustomTabs
                  textColor="primary"
                  value={currentLeftTab}
                  onChange={(_, value) => setCurrentLeftTab(value)}
                  TabIndicatorProps={{
                    style: {
                      backgroundColor: theme.palette.primary.main,
                      opacity: 0.08,
                      height: "100%",
                      borderRadius: "4px",
                      cursor: "pointer",
                    },
                  }}
                >
                  <CustomTab
                    label="Transcript"
                    value="Transcript"
                    disabled={false}
                    sx={{ paddingY: "4px" }}
                  />
                </CustomTabs>
              </TabWrapper>
              <ShowComponent condition={currentLeftTab === "Transcript"}>
                <Box
                  border="1px solid"
                  borderColor="divider"
                  borderRadius={1}
                  p={1}
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 1,
                    flexGrow: 1,
                    overflow: "auto",
                    mb: 2,
                    width: "100%",
                  }}
                >
                  {filteredTranscript?.length > 0 ? (
                    <CallLogTranscript transcript={filteredTranscript} />
                  ) : (
                    <Box
                      justifyContent="center"
                      alignItems="center"
                      display="flex"
                      height={185}
                    >
                      <Typography typography="s2_1">
                        Transcript is empty
                      </Typography>
                    </Box>
                  )}
                </Box>
              </ShowComponent>
            </Box>
          </ShowComponent>
          <ShowComponent condition={data?.module === "project"}>
            <LeftSection data={data} />
          </ShowComponent>
        </Grid>
        <Grid item xs={6} md={6} height="100%">
          <ShowComponent condition={data?.module === "simulate"}>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 0.5,
              }}
            >
              <TestDetailDrawerRightSection
                scenarioId={scenarioId}
                openedExecutionId={data?.id}
              />
            </Box>
          </ShowComponent>
          <ShowComponent condition={data?.module === "project"}>
            <RightSection data={data} />
          </ShowComponent>
        </Grid>
      </Grid>

      <Drawer
        anchor="right"
        open={annotationSidebarOpen}
        onClose={() => setAnnotationSidebarOpen(false)}
        PaperProps={{
          sx: { width: 360, zIndex: 20 },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <AnnotationSidebarContent
          sources={annotationSources}
          onClose={() => setAnnotationSidebarOpen(false)}
          onAddLabel={
            urlAgentDefinitionId || resolvedProjectId
              ? () => setAddLabelDrawerOpen(true)
              : undefined
          }
          onScoresChanged={() => {
            queryClient.invalidateQueries({ queryKey: ["scores"] });
            queryClient.invalidateQueries({ queryKey: ["call-detail-logs"] });
            queryClient.invalidateQueries({
              queryKey: ["annotation-queues", "for-source"],
            });
          }}
        />
      </Drawer>

      <AddLabelDrawer
        open={addLabelDrawerOpen}
        onClose={() => setAddLabelDrawerOpen(false)}
        agentDefinitionId={urlAgentDefinitionId}
        projectId={resolvedProjectId}
        onLabelsChanged={() => {
          queryClient.invalidateQueries({
            queryKey: ["annotation-queues"],
          });
          queryClient.invalidateQueries({
            queryKey: ["annotation-queues", "for-source"],
          });
        }}
      />
    </Box>
  );
};

CallLogSideDrawerChild.propTypes = {
  data: PropTypes.object,
};

const CallLogDetailDrawer = () => {
  const { callLogsSideDrawerData, setCallLogsSideDrawerData } =
    useCallLogsSideDrawerStore();

  return (
    <Drawer
      open={!!callLogsSideDrawerData}
      onClose={() => setCallLogsSideDrawerData(null)}
      anchor="right"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 10,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "0px !important",
          backgroundColor: "background.paper",
          display: "flex",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: {
            backgroundColor: "transparent",
            borderRadius: "0px !important",
          },
        },
      }}
    >
      <CallLogSideDrawerChild data={callLogsSideDrawerData} />
    </Drawer>
  );
};

export default CallLogDetailDrawer;
