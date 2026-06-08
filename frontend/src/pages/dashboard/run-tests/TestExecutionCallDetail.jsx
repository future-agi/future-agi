import React, { useEffect, useMemo } from "react";
import Box from "@mui/material/Box";
import { Helmet } from "react-helmet-async";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import CallDetails from "src/sections/test-detail/CallDetails";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildVoiceCallReviewedPayload,
  buildVoiceSuccessCriteriaHref,
  getVoiceOnboardingParams,
  voiceSetupQuickStartAttributionFromSearch,
  VOICE_ONBOARDING_MODES,
} from "src/sections/test/onboardingVoiceRouteEvents";
import { agentSetupQuickStartAttributionFromSearch } from "src/sections/agent-playground/agentOnboardingEvents";
import { setupQuickStartAttributionParams } from "src/sections/auth/jwt/setup-org-quick-starts";
import TestOnboardingFocusPanel from "src/sections/test/TestOnboardingFocusPanel";

const TestExecutionCallDetail = () => {
  const { testId, executionId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const voiceParams = useMemo(
    () => getVoiceOnboardingParams(location.search),
    [location.search],
  );
  const voiceQuickStartAttribution = useMemo(
    () => voiceSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const agentQuickStartAttribution = useMemo(
    () => agentSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const isVoiceReview = voiceParams.mode === VOICE_ONBOARDING_MODES.REVIEW_CALL;
  const isAgentOnboardingReview =
    voiceParams.from === "onboarding" && !isVoiceReview;
  const successCriteriaHref = useMemo(
    () =>
      buildVoiceSuccessCriteriaHref({
        agentDefinitionId: voiceParams.agentDefinitionId,
        callId: voiceParams.callId,
        quickStartAttribution: voiceQuickStartAttribution,
        search: location.search,
        testId,
      }),
    [
      location.search,
      testId,
      voiceParams.agentDefinitionId,
      voiceParams.callId,
      voiceQuickStartAttribution,
    ],
  );

  useEffect(() => {
    if (!testId || !executionId) return;

    if (isVoiceReview) {
      recordActivationEvent?.(
        buildVoiceCallReviewedPayload({
          testId,
          executionId,
          callId: voiceParams.callId,
          quickStartAttribution: voiceQuickStartAttribution,
        }),
      );
      return;
    }

    if (!isAgentOnboardingReview) return;
    recordActivationEvent?.({
      eventName: "agent_trace_reviewed",
      primaryPath: "agent",
      stage: "review_agent_trace",
      source: "simulate",
      artifactType: "test_execution",
      artifactId: executionId,
      metadata: {
        test_id: testId,
        execution_id: executionId,
      },
      idempotencyKey: `agent_trace_reviewed:${testId}:${executionId}:call-details`,
      isSample: false,
      ...setupQuickStartAttributionParams(agentQuickStartAttribution),
    });
  }, [
    agentQuickStartAttribution,
    executionId,
    isAgentOnboardingReview,
    isVoiceReview,
    recordActivationEvent,
    testId,
    voiceQuickStartAttribution,
    voiceParams.callId,
  ]);

  return (
    <>
      <Helmet>
        <title>Call Details</title>
      </Helmet>
      {isVoiceReview ? (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
            height: "100%",
            minHeight: 0,
            p: 2,
          }}
        >
          <TestOnboardingFocusPanel
            currentStep="Review call"
            description="Inspect the transcript, recording, latency, and outcome. Next, add success criteria so future voice calls are scored after each run."
            eyebrow="Voice setup"
            primaryAction={{
              label: "Add success criteria",
              onClick: () => {
                if (successCriteriaHref) {
                  navigate(successCriteriaHref);
                }
              },
              disabled: !successCriteriaHref,
            }}
            singleActionFocus
            steps={[
              { label: "Test call", complete: true },
              { label: "Review call", complete: true },
              { label: "Success criteria", complete: false },
            ]}
            title="Review the voice test call"
            tourAnchor={voiceParams.tourAnchor}
          />
          <Box sx={{ flex: 1, minHeight: 0 }}>
            <CallDetails />
          </Box>
        </Box>
      ) : (
        <CallDetails />
      )}
    </>
  );
};

export default TestExecutionCallDetail;
