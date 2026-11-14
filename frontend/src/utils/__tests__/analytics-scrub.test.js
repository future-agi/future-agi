import { describe, expect, it } from "vitest";
import {
  isCredentialLikeKey,
  scrubAnalyticsProperties,
} from "../analytics-scrub";

const VALUE = "synthetic-value-0000";

describe("scrubAnalyticsProperties", () => {
  it.each([
    "api_key",
    "apiKey",
    "API_KEY",
    "x-api-key",
    "livekit_api_key",
    "livekitApiKey",
    "livekit_api_secret",
    "client_secret",
    "secret",
    "secretRef",
    "token",
    "access_token",
    "accessToken",
    "refreshToken",
    "onboarding_token",
    "password",
    "confirmPassword",
    "repeat_password",
    "Authorization",
    "authorization_header",
    "headers",
    "cookie",
    "Set-Cookie",
    "credentials",
    "key",
    "private_key",
    "awsSecretAccessKey",
    "aws_access_key_id",
    "accessKeyId",
    "bearer",
    "bearer_token",
  ])("drops the credential-like key %s", (key) => {
    expect(isCredentialLikeKey(key)).toBe(true);
    expect(scrubAnalyticsProperties({ [key]: VALUE, kept: 1 })).toEqual({
      kept: 1,
    });
  });

  it.each([
    "agent_type",
    "provider",
    "authentication_method",
    "model_name",
    "max_tokens",
    "maxTokens",
    "total_tokens",
    "input_token_cost",
    "outputTokenCost",
    "key_id",
    "keyId",
    "headerName",
    "sort_order",
    "form_fields",
    "id",
  ])("keeps the ordinary key %s", (key) => {
    expect(isCredentialLikeKey(key)).toBe(false);
    expect(scrubAnalyticsProperties({ [key]: "x" })).toEqual({ [key]: "x" });
  });

  it("drops credential-like keys at any depth, inside objects and arrays", () => {
    const properties = {
      form_fields: {
        agent_type: "voice",
        api_key: VALUE,
        headers: [{ key: "Authorization", value: `Bearer ${VALUE}` }],
        nested: {
          livekit_api_secret: VALUE,
          items: [{ token: VALUE, name: "first" }, [{ password: VALUE }]],
        },
      },
      count: 3,
    };

    const scrubbed = scrubAnalyticsProperties(properties);

    expect(scrubbed).toEqual({
      form_fields: {
        agent_type: "voice",
        nested: { items: [{ name: "first" }, [{}]] },
      },
      count: 3,
    });
    expect(JSON.stringify(scrubbed)).not.toContain(VALUE);
  });

  it("returns a new object and leaves the input unchanged", () => {
    const properties = {
      form_fields: { api_key: VALUE, list: [{ secret: VALUE, n: 1 }] },
    };
    const before = structuredClone(properties);

    const scrubbed = scrubAnalyticsProperties(properties);

    expect(properties).toEqual(before);
    expect(scrubbed).not.toBe(properties);
    expect(scrubbed.form_fields).not.toBe(properties.form_fields);
    expect(scrubbed.form_fields.list).not.toBe(properties.form_fields.list);
  });

  it("keeps true/false flags under credential-like names", () => {
    expect(
      scrubAnalyticsProperties({
        form_fields: { password_match: true, new_password_entered: false },
      }),
    ).toEqual({
      form_fields: { password_match: true, new_password_entered: false },
    });
  });

  it("keeps scalar values, null and undefined as they are", () => {
    expect(scrubAnalyticsProperties(undefined)).toBeUndefined();
    expect(scrubAnalyticsProperties(null)).toBeNull();
    expect(scrubAnalyticsProperties("label")).toBe("label");
    expect(
      scrubAnalyticsProperties({ a: null, b: undefined, c: 0, d: [1, "x"] }),
    ).toEqual({ a: null, b: undefined, c: 0, d: [1, "x"] });
  });

  it("keeps each array element at its position", () => {
    // eslint-disable-next-line no-sparse-arrays
    const scrubbed = scrubAnalyticsProperties({ values: [, "second", null] });

    expect(scrubbed.values).toHaveLength(3);
    expect(scrubbed.values[1]).toBe("second");
    expect(scrubbed.values[2]).toBeNull();
  });

  it("stops at a reference back to an enclosing object", () => {
    const properties = { name: "loop", api_key: VALUE };
    properties.self = properties;

    expect(scrubAnalyticsProperties(properties)).toEqual({ name: "loop" });
  });
});
