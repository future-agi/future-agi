import React, { useEffect, useMemo } from "react";
import { Helmet } from "react-helmet-async";
import { useLocation, useParams } from "react-router-dom";
import CallDetails from "src/sections/test-detail/CallDetails";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildVoiceCallReviewedPayload,
  getVoiceOnboardingParams,
  voiceSetupQuickStartAttributionFromSearch,
  VOICE_ONBOARDING_MODES,
} from "src/sections/test/onboardingVoiceRouteEvents";
import { agentSetupQuickStartAttributionFromSearch } from "src/sections/agent-playground/agentOnboardingEvents";
import { setupQuickStartAttributionParams } from "src/sections/auth/jwt/setup-org-quick-starts";

const TestExecutionCallDetail = () => {
  const { testId, executionId } = useParams();
  const location = useLocation();
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
      <CallDetails />
    </>
  );
};

export default TestExecutionCallDetail;
