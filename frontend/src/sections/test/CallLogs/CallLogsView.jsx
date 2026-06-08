import { Box, Button } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import CallLogsHeader from "./CallLogsHeader";
import CallLogsCard from "./CallLogsCard";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import { useScrollEnd } from "../../../hooks/use-scroll-end";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios from "../../../utils/axios";
import { endpoints } from "../../../utils/axios";
import { useLocation, useNavigate, useParams } from "react-router";
import { useDebounce } from "src/hooks/use-debounce";
import { ShowComponent } from "src/components/show";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildVoiceMonitorOpenedPayload,
  buildVoiceRouteFocusPayload,
  getVoiceOnboardingParams,
  voiceSetupQuickStartAttributionFromSearch,
  VOICE_ONBOARDING_MODES,
} from "../onboardingVoiceRouteEvents";
import TestOnboardingFocusPanel from "../TestOnboardingFocusPanel";

const CallLogsView = () => {
  const { testId } = useParams();
  const [searchText, setSearchText] = useState("");
  const { role } = useAuthContext();
  const navigate = useNavigate();
  const location = useLocation();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const recordedFocusRef = useRef(false);
  const voiceParams = useMemo(
    () => getVoiceOnboardingParams(location.search),
    [location.search],
  );
  const voiceQuickStartAttribution = useMemo(
    () => voiceSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const isMonitorCallsMode =
    voiceParams.mode === VOICE_ONBOARDING_MODES.MONITOR_CALLS;

  useEffect(() => {
    if (!isMonitorCallsMode || !testId || recordedFocusRef.current) return;
    recordedFocusRef.current = true;
    recordActivationEvent?.(
      buildVoiceRouteFocusPayload({
        mode: voiceParams.mode,
        quickStartAttribution: voiceQuickStartAttribution,
        source: "voice_call_logs",
        testId,
      }),
    );
    recordActivationEvent?.(
      buildVoiceMonitorOpenedPayload({
        quickStartAttribution: voiceQuickStartAttribution,
        testId,
        source: "voice_call_logs",
      }),
    );
  }, [
    isMonitorCallsMode,
    recordActivationEvent,
    testId,
    voiceQuickStartAttribution,
    voiceParams.mode,
  ]);

  const debouncedSearchText = useDebounce(searchText, 500);

  const { data, isFetchingNextPage, fetchNextPage, isPending } =
    useInfiniteQuery({
      queryFn: ({ pageParam }) =>
        axios.get(endpoints.runTests.callExecutionsByTestRunId(testId), {
          params: { page: pageParam, search: debouncedSearchText },
        }),
      queryKey: ["test-runs-call-logs", testId, debouncedSearchText],
      getNextPageParam: ({ data }) =>
        data?.next ? data?.current_page + 1 : null,
      initialPageParam: 1,
    });
  const callLogs = useMemo(
    () => data?.pages.flatMap((page) => page.data.results),
    [data],
  );
  const testRunsHref = `/dashboard/simulate/test/${testId}/runs${location.search || ""}`;

  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);

  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
      }}
    >
      <TestOnboardingFocusPanel
        currentStep="Monitor calls"
        description="Use call logs to keep watching transcripts, recordings, interruptions, and saved criteria after the first setup."
        eyebrow="Voice setup"
        hidden={!isMonitorCallsMode}
        primaryAction={{
          label: callLogs?.length ? "Run another test call" : "Run test call",
          onClick: () => navigate(testRunsHref),
        }}
        singleActionFocus
        steps={[
          { label: "Test call", complete: true },
          { label: "Review call", complete: true },
          { label: "Success criteria", complete: true },
          { label: "Monitor calls", complete: Boolean(callLogs?.length) },
        ]}
        title="Monitor voice calls"
      />
      <CallLogsHeader searchText={searchText} setSearchText={setSearchText} />
      <ShowComponent
        condition={
          !isPending && callLogs.length === 0 && debouncedSearchText === ""
        }
      >
        <EmptyLayout
          title={
            isMonitorCallsMode ? "No voice call logs yet" : "No Call Logs Found"
          }
          description={
            isMonitorCallsMode
              ? "Run a test call to start monitoring transcripts, recordings, and criteria results."
              : "Get started by running a test"
          }
          action={
            <Button
              variant="contained"
              onClick={() => {
                navigate(testRunsHref);
              }}
              sx={{
                bgcolor: "primary.main",
                "&:hover": {
                  bgcolor: "primary.dark",
                },
              }}
              disabled={
                !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
              }
            >
              Run New Test
            </Button>
          }
          hideIcon
        />
      </ShowComponent>

      <AudioPlaybackProvider>
        <Box
          ref={scrollContainer}
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            overflowY: "auto",
          }}
        >
          {callLogs?.map((item) => (
            <CallLogsCard key={item.id} log={item} />
          ))}
        </Box>
      </AudioPlaybackProvider>
    </Box>
  );
};

export default CallLogsView;
