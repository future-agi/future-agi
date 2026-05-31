import React, { useCallback, useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { Helmet } from "react-helmet-async";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import {
  appendSetupQuickStartAttributionToHref,
  normalizeSetupQuickStartAttribution,
} from "src/sections/auth/jwt/setup-org-quick-starts";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildObserveEvaluatorCreateHref,
  getObserveTraceReviewOnboardingParams,
  OBSERVE_ONBOARDING_MODES,
} from "src/sections/projects/observeOnboardingRoute";

export default function TraceFullPage() {
  const { observeId, traceId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const recordedTraceRef = useRef(null);
  const sampleTraceSearchContext = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return {
      isSampleTrace: params.get("sample") === "true",
      quickStartAttribution: normalizeSetupQuickStartAttribution({
        quickStartGoal: params.get("quick_start_goal"),
        quickStartId: params.get("quick_start_id"),
        quickStartPrimaryPath: params.get("quick_start_primary_path"),
      }),
    };
  }, [location.search]);
  const { isSampleTrace, quickStartAttribution } = sampleTraceSearchContext;
  const traceReviewOnboardingParams = useMemo(
    () => getObserveTraceReviewOnboardingParams(location.search),
    [location.search],
  );
  const isTraceReviewOnboarding =
    !isSampleTrace &&
    traceReviewOnboardingParams.mode ===
      OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE;
  const realSetupHref = useMemo(
    () =>
      appendSetupQuickStartAttributionToHref(
        "/dashboard/observe?setup=true&source=sample_trace_review",
        quickStartAttribution,
      ),
    [quickStartAttribution],
  );

  const handleConnectRealApp = useCallback(() => {
    recordActivationEvent({
      eventName: "sample_to_real_setup_clicked",
      primaryPath: "sample",
      stage: "connect_real_data",
      source: "sample_trace_full_page",
      artifactType: "trace",
      artifactId: traceId,
      projectId: observeId,
      isSample: true,
      ...quickStartAttribution,
      metadata: {
        entry: "trace_full_page",
        target_route: realSetupHref,
      },
    });
    navigate(realSetupHref);
  }, [
    navigate,
    observeId,
    quickStartAttribution,
    realSetupHref,
    recordActivationEvent,
    traceId,
  ]);

  const handleCreateEvaluator = useCallback(() => {
    navigate(buildObserveEvaluatorCreateHref({ observeId }));
  }, [navigate, observeId]);

  const handleClose = useCallback(() => {
    if (window.history.length > 1) {
      navigate(-1);
    } else if (observeId) {
      navigate(`/dashboard/observe/${observeId}/llm-tracing`);
    } else {
      window.close();
    }
  }, [navigate, observeId]);

  useEffect(() => {
    if (!observeId || !traceId) return;

    const recordKey = `${observeId}:${traceId}`;
    if (recordedTraceRef.current === recordKey) return;
    recordedTraceRef.current = recordKey;

    recordActivationEvent({
      eventName: isSampleTrace
        ? "sample_trace_detail_opened"
        : "trace_detail_opened",
      primaryPath: isSampleTrace ? "sample" : "observe",
      stage: "review_first_trace",
      source: isSampleTrace ? "sample_trace_full_page" : "trace_full_page",
      artifactType: "trace",
      artifactId: traceId,
      projectId: observeId,
      isSample: isSampleTrace,
      ...quickStartAttribution,
      metadata: {
        entry: "trace_full_page",
        is_sample_route: isSampleTrace,
      },
    });
  }, [
    isSampleTrace,
    observeId,
    quickStartAttribution,
    recordActivationEvent,
    traceId,
  ]);

  const onboardingBanner = useMemo(() => {
    if (isSampleTrace) {
      return {
        title: "Sample trace review",
        description:
          "This is the review surface. Connect your app next to send the first real trace.",
        primaryAction: {
          label: "Connect your app",
          onClick: handleConnectRealApp,
        },
      };
    }

    if (isTraceReviewOnboarding) {
      return {
        title: "First trace received",
        description:
          "Review spans, latency, cost, and model inputs here. When this signal looks right, create an evaluator.",
        primaryAction: {
          label: "Create evaluator",
          onClick: handleCreateEvaluator,
        },
      };
    }

    return undefined;
  }, [
    handleConnectRealApp,
    handleCreateEvaluator,
    isSampleTrace,
    isTraceReviewOnboarding,
  ]);

  return (
    <>
      <Helmet>
        <title>Trace — {traceId?.substring(0, 8) || "..."}</title>
      </Helmet>
      <TraceDetailDrawerV2
        open
        traceId={traceId}
        projectId={observeId}
        onClose={handleClose}
        hasPrev={false}
        hasNext={false}
        initialFullscreen
        onboardingBanner={onboardingBanner}
      />
    </>
  );
}
