import React, { useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { useLocation, useParams } from "react-router-dom";
import CallDetails from "src/sections/test-detail/CallDetails";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";

const TestExecutionCallDetail = () => {
  const { testId, executionId } = useParams();
  const location = useLocation();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const isOnboardingReview =
    new URLSearchParams(location.search).get("from") === "onboarding";

  useEffect(() => {
    if (!isOnboardingReview || !testId || !executionId) return;
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
    });
  }, [executionId, isOnboardingReview, recordActivationEvent, testId]);

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
