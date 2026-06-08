import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildVoiceReviewCallHref,
  buildVoiceTestCallCompletedPayload,
  VOICE_ONBOARDING_MODES,
  voiceCallIdFromExecution,
} from "../onboardingVoiceRouteEvents";

const TERMINAL_RUN_STATUSES = new Set(["cancelled", "completed", "failed"]);

const firstTerminalExecution = (executions = []) =>
  executions.find((execution) =>
    TERMINAL_RUN_STATUSES.has(String(execution?.status || "").toLowerCase()),
  );

export default function useVoiceOnboardingRunCompletion({
  agentType,
  executions = [],
  quickStartAttribution,
  testId,
  voiceParams,
} = {}) {
  const navigate = useNavigate();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const recordedKeysRef = useRef(new Set());

  useEffect(() => {
    if (voiceParams?.mode !== VOICE_ONBOARDING_MODES.RUN_TEST_CALL) return;
    if ((agentType ?? AGENT_TYPES.VOICE) !== AGENT_TYPES.VOICE) return;
    if (!testId) return;

    const execution = firstTerminalExecution(executions);
    if (!execution?.id) return;

    const callId = voiceCallIdFromExecution(execution);
    const payload = buildVoiceTestCallCompletedPayload({
      agentDefinitionId: voiceParams.agentDefinitionId,
      callId,
      executionId: execution.id,
      quickStartAttribution,
      status: execution.status,
      testId,
    });

    if (recordedKeysRef.current.has(payload.idempotencyKey)) return;
    recordedKeysRef.current.add(payload.idempotencyKey);
    recordActivationEvent?.(payload, {
      onError: () => {
        recordedKeysRef.current.delete(payload.idempotencyKey);
      },
    });

    const reviewHref = buildVoiceReviewCallHref({
      agentDefinitionId: voiceParams.agentDefinitionId,
      callId,
      executionId: execution.id,
      quickStartAttribution,
      testId,
    });
    if (reviewHref) navigate(reviewHref, { replace: true });
  }, [
    agentType,
    executions,
    navigate,
    quickStartAttribution,
    recordActivationEvent,
    testId,
    voiceParams,
  ]);
}
