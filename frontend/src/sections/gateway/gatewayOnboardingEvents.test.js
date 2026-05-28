import { describe, expect, it } from "vitest";
import {
  buildGatewayFallbackPolicyCreatedPayload,
  buildGatewayPolicyCreatedPayload,
} from "./gatewayOnboardingEvents";

describe("gatewayOnboardingEvents", () => {
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
});
