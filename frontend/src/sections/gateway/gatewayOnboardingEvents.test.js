import { describe, expect, it } from "vitest";
import {
  buildGatewayRequestReviewHref,
  buildGatewayRequestSeenPayload,
  buildGatewayFallbackPolicyCreatedPayload,
  buildGatewayOnboardingCompletionHref,
  buildGatewayPolicyCreatedPayload,
  GATEWAY_ONBOARDING_MODES,
  gatewayPlaygroundRequestId,
  getGatewayOnboardingRouteParams,
} from "./gatewayOnboardingEvents";

describe("gatewayOnboardingEvents", () => {
  it("parses gateway journey-step params from Home CTAs", () => {
    [
      ["configure_gateway_provider", GATEWAY_ONBOARDING_MODES.ADD_PROVIDER],
      ["create_gateway_key", GATEWAY_ONBOARDING_MODES.CREATE_KEY],
      ["run_gateway_request", GATEWAY_ONBOARDING_MODES.TEST_REQUEST],
      ["review_gateway_log", GATEWAY_ONBOARDING_MODES.REVIEW_REQUEST],
      ["fix_gateway_failure", GATEWAY_ONBOARDING_MODES.FIX_FAILURE],
      ["add_gateway_policy", GATEWAY_ONBOARDING_MODES.ADD_POLICY],
    ].forEach(([journeyStep, mode]) => {
      expect(
        getGatewayOnboardingRouteParams(
          `?tour_anchor=gateway_focus&journey_step=${journeyStep}&request_id=req-123`,
        ),
      ).toEqual({
        isOnboarding: true,
        mode,
        requestId: "req-123",
      });
    });
  });

  it("builds a gateway first-request payload from playground results", () => {
    expect(
      buildGatewayRequestSeenPayload({
        gatewayId: "gateway-1",
        result: {
          status_code: 200,
          body: { id: "chatcmpl-1" },
          guardrail_headers: {
            "x-agentcc-request-id": "req-123",
          },
          model: "gpt-4o-mini",
          blocked: false,
          warned: false,
        },
      }),
    ).toMatchObject({
      eventName: "gateway_request_seen",
      primaryPath: "gateway",
      stage: "run_gateway_request",
      source: "gateway_request_onboarding",
      artifactType: "gateway_request",
      artifactId: "req-123",
      metadata: {
        gateway_id: "gateway-1",
        request_id: "req-123",
        response_id: "chatcmpl-1",
        status_code: 200,
        is_error: false,
        model: "gpt-4o-mini",
        blocked: false,
        warned: false,
      },
      idempotencyKey: "gateway_request_seen:req-123:gateway-1",
      isSample: false,
    });
  });

  it("extracts request IDs from playground response shapes", () => {
    expect(gatewayPlaygroundRequestId({ request_id: "req-top" })).toBe(
      "req-top",
    );
    expect(
      gatewayPlaygroundRequestId({
        guardrailHeaders: {
          "X-Request-ID": "req-header",
        },
      }),
    ).toBe("req-header");
  });

  it("builds gateway request-review routes", () => {
    expect(buildGatewayRequestReviewHref({ requestId: "req-123" })).toBe(
      "/dashboard/gateway/logs?onboarding=review-request&request_id=req-123",
    );
    expect(buildGatewayRequestReviewHref()).toBe(
      "/dashboard/gateway/logs?onboarding=review-request",
    );
  });

  it("builds a safe gateway fallback policy completion payload", () => {
    expect(
      buildGatewayFallbackPolicyCreatedPayload({
        gatewayId: "gateway-1",
        requestId: "req-123",
        routing: {
          fallback_enabled: true,
          model_fallbacks: {
            "gpt-4o": ["gpt-4o-mini"],
          },
        },
      }),
    ).toMatchObject({
      eventName: "gateway_policy_created",
      primaryPath: "gateway",
      stage: "add_gateway_policy",
      source: "gateway_fallbacks_onboarding",
      artifactType: "gateway_policy",
      artifactId: "req-123",
      metadata: {
        gateway_id: "gateway-1",
        request_id: "req-123",
        policy_type: "fallback",
        policy_id: "fallback:req-123",
        gateway_synced: true,
        fallback_chain_count: 1,
        fallback_enabled: true,
      },
      idempotencyKey: "gateway_policy_created:fallback:req-123:gateway-1",
      isSample: false,
    });
  });

  it("keeps idempotency keys bounded when request IDs are long", () => {
    const payload = buildGatewayFallbackPolicyCreatedPayload({
      requestId: "request-".repeat(40),
      routing: {},
    });

    expect(payload.idempotencyKey.length).toBeLessThanOrEqual(160);
  });

  it("builds generic gateway policy completion payloads", () => {
    expect(
      buildGatewayPolicyCreatedPayload({
        gatewayId: "gateway-1",
        policyId: "budget:per_model",
        policyType: "budget",
        requestId: "req-123",
        source: "gateway_budget_onboarding",
        metadata: {
          budget_level: "per_model",
          limit: 1000,
        },
      }),
    ).toMatchObject({
      eventName: "gateway_policy_created",
      primaryPath: "gateway",
      stage: "add_gateway_policy",
      source: "gateway_budget_onboarding",
      metadata: {
        gateway_id: "gateway-1",
        request_id: "req-123",
        policy_type: "budget",
        policy_id: "budget:per_model",
        gateway_synced: true,
        budget_level: "per_model",
        limit: 1000,
      },
      idempotencyKey: "gateway_policy_created:budget:req-123:gateway-1",
    });
  });

  it("builds the gateway onboarding completion destination", () => {
    expect(buildGatewayOnboardingCompletionHref()).toBe(
      "/dashboard/home?mode=daily-quality&source=onboarding&target_event=gateway_policy_created",
    );
  });
});
