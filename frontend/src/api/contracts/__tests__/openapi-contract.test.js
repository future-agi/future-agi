import { describe, expect, it } from "vitest";

import {
  findOpenApiEndpoint,
  validateContractedRequestConfig,
  validateContractedResponse,
} from "../openapi-contract";

describe("OpenAPI runtime contract", () => {
  it("finds endpoints across the full Management API surface", () => {
    expect(findOpenApiEndpoint("/usage/ee/licenses/", "get")).toMatchObject({
      template: "/usage/ee/licenses/",
      method: "get",
    });
    expect(
      findOpenApiEndpoint("/falcon-ai/conversations/", "post"),
    ).toMatchObject({
      template: "/falcon-ai/conversations/",
      method: "post",
    });
    expect(
      findOpenApiEndpoint("/model-hub/annotation-queues/queue-1/items/", "get"),
    ).toMatchObject({
      template: "/model-hub/annotation-queues/{queue_id}/items/",
      method: "get",
    });
  });

  it("validates request bodies from backend serializer schemas", () => {
    expect(
      validateContractedRequestConfig({
        url: "/usage/ee/licenses/",
        method: "post",
        data: { band: "team", billing_interval: "monthly" },
      }),
    ).toMatchObject({ ok: true });

    const result = validateContractedRequestConfig({
      url: "/usage/ee/licenses/",
      method: "post",
      data: { band: "legacy-plan" },
    });

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "request body contract validation failed",
    );
  });

  it("validates form bodies against the same request schema", () => {
    const body = new FormData();
    body.set("band", "business");
    body.set("billing_interval", "yearly");

    expect(
      validateContractedRequestConfig({
        url: "/usage/ee/licenses/",
        method: "post",
        data: body,
      }),
    ).toMatchObject({ ok: true });
  });

  it("keeps form-body coercion isolated from JSON request validation", () => {
    const body = new FormData();
    body.set("require_2fa", "true");
    body.set("require_2fa_grace_period_days", "7");

    expect(
      validateContractedRequestConfig({
        url: "/accounts/organization/2fa-policy/",
        method: "put",
        data: body,
      }),
    ).toMatchObject({ ok: true });

    const result = validateContractedRequestConfig({
      url: "/accounts/organization/2fa-policy/",
      method: "put",
      data: {
        require_2fa: true,
        require_2fa_grace_period_days: "7",
      },
    });

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "request body contract validation failed",
    );
  });

  it("rejects unknown query params from the generated endpoint schema", () => {
    expect(
      validateContractedRequestConfig({
        url: "/model-hub/annotation-queues/?status=active&legacyStatus=active",
        method: "get",
      }).ok,
    ).toBe(false);

    expect(
      validateContractedRequestConfig({
        url: "/model-hub/annotation-queues/",
        method: "get",
        params: { status: "active", legacyStatus: "active" },
      }).ok,
    ).toBe(false);
  });

  it("rejects query params on endpoints that do not document any", () => {
    const result = validateContractedRequestConfig({
      url: "/usage/ee/licenses/?legacy=true",
      method: "post",
      data: { band: "team", billing_interval: "monthly" },
    });

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain("query contract validation failed");
  });

  it("validates list query params the way DRF query serializers receive them", () => {
    expect(
      validateContractedRequestConfig({
        url: "/accounts/organization/members/?filter_status=Active",
        method: "get",
      }),
    ).toMatchObject({ ok: true });

    expect(
      validateContractedRequestConfig({
        url: "/accounts/organization/members/?filter_status=Active&filter_status=Pending",
        method: "get",
      }),
    ).toMatchObject({ ok: true });
  });

  it("does not unwrap response envelopes to hide schema drift", () => {
    const response = {
      status: 200,
      config: { url: "/usage/ee/licenses/", method: "get" },
      data: {
        result: {
          licenses: [],
        },
      },
    };

    const result = validateContractedResponse(response);

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "response contract validation failed",
    );
    expect(result.error.message).toContain("status");
  });

  it("does not validate undocumented error responses against a success schema", () => {
    const response = {
      status: 404,
      config: {
        url: "/usage/ee/licenses/2db3e0e8-5cec-4bb3-a358-ff1ea0671599/revoke/",
        method: "post",
      },
      data: {
        status: false,
        result: "License not found",
      },
    };

    expect(validateContractedResponse(response)).toMatchObject({
      ok: true,
      skipped: true,
    });
  });
});
