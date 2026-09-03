import { describe, expect, it } from "vitest";
import { normalizeGateway } from "./GatewayContext";

describe("normalizeGateway", () => {
  it("maps canonical snake_case gateway fields to UI field names", () => {
    const normalized = normalizeGateway({
      id: "default",
      name: "Agent Command Center Gateway",
      base_url: "http://localhost:8080/v1",
      provider_count: 2,
      model_count: 5,
      last_health_check: "2026-05-25T01:02:03Z",
      status: "healthy",
    });

    expect(normalized).toMatchObject({
      id: "default",
      name: "Agent Command Center Gateway",
      baseUrl: "http://localhost:8080/v1",
      providerCount: 2,
      modelCount: 5,
      lastHealthCheck: "2026-05-25T01:02:03Z",
      status: "healthy",
    });
    expect(normalized.base_url).toBe("http://localhost:8080/v1");
  });

  it("preserves existing camelCase values when both shapes are present", () => {
    const normalized = normalizeGateway({
      baseUrl: "https://ui.example/v1",
      base_url: "https://api.example/v1",
      providerCount: 7,
      provider_count: 2,
      modelCount: 11,
      model_count: 5,
      lastHealthCheck: "2026-05-25T04:05:06Z",
      last_health_check: "2026-05-25T01:02:03Z",
    });

    expect(normalized).toMatchObject({
      baseUrl: "https://ui.example/v1",
      providerCount: 7,
      modelCount: 11,
      lastHealthCheck: "2026-05-25T04:05:06Z",
    });
  });
});
