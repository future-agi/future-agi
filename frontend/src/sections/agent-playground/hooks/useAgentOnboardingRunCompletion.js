import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildAgentEvalCreatedPayload,
  buildAgentOnboardingReturnHref,
  buildAgentPrototypeRunCompletedPayload,
  buildAgentReviewRunHref,
} from "../agentOnboardingEvents";

const TERMINAL_RUN_STATUSES = new Set(["error", "failed", "success"]);

export default function useAgentOnboardingRunCompletion({
  agentId,
  executionData,
  executionId,
  onboardingMode,
  quickStartAttribution,
  versionId,
} = {}) {
  const navigate = useNavigate();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const recordedKeysRef = useRef(new Set());

  useEffect(() => {
    const status = executionData?.status?.toLowerCase?.();
    const isScenarioRun = onboardingMode === "run-scenario";
    const isEvalCoverageRun = onboardingMode === "add-eval";
    if (!isScenarioRun && !isEvalCoverageRun) return;
    if (!agentId || !executionId || !TERMINAL_RUN_STATUSES.has(status)) return;

    const payload = isEvalCoverageRun
      ? buildAgentEvalCreatedPayload({
          agentId,
          executionId,
          quickStartAttribution,
          status,
          versionId,
        })
      : buildAgentPrototypeRunCompletedPayload({
          agentId,
          executionId,
          quickStartAttribution,
          status,
          versionId,
        });
    if (recordedKeysRef.current.has(payload.idempotencyKey)) return;

    recordedKeysRef.current.add(payload.idempotencyKey);
    recordActivationEvent?.(payload, {
      onError: () => {
        recordedKeysRef.current.delete(payload.idempotencyKey);
      },
    });

    navigate(
      isEvalCoverageRun
        ? buildAgentOnboardingReturnHref({
            ...payload,
            quickStartAttribution,
          })
        : buildAgentReviewRunHref({
            agentId,
            quickStartAttribution,
            versionId,
          }),
      { replace: true },
    );
  }, [
    agentId,
    executionData,
    executionId,
    navigate,
    onboardingMode,
    quickStartAttribution,
    recordActivationEvent,
    versionId,
  ]);
}
