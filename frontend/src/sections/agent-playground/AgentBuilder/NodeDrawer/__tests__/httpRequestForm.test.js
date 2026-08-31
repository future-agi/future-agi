import { describe, it, expect } from "vitest";
import {
  httpRequestNodeFormSchema,
  getNodeFormSchema,
} from "../forms/nodeFormSchemas";
import { getDefaultValues } from "../nodeFormUtils";
import { NODE_TYPES } from "../../../utils/constants";

const validForm = {
  name: "fetch_users",
  method: "GET",
  url: "https://api.example.com/users",
  headers: {},
  body: "",
  authType: "none",
  authToken: "",
  authUsername: "",
  authPassword: "",
  timeout: 30,
  retries: 0,
};

describe("httpRequestNodeFormSchema", () => {
  it("accepts a valid GET request form", () => {
    const result = httpRequestNodeFormSchema.safeParse(validForm);
    expect(result.success).toBe(true);
  });

  it("rejects an empty url", () => {
    const result = httpRequestNodeFormSchema.safeParse({
      ...validForm,
      url: "",
    });
    expect(result.success).toBe(false);
  });

  it("rejects a url without http(s) scheme", () => {
    const result = httpRequestNodeFormSchema.safeParse({
      ...validForm,
      url: "ftp://example.com",
    });
    expect(result.success).toBe(false);
  });

  it("rejects an invalid method", () => {
    const result = httpRequestNodeFormSchema.safeParse({
      ...validForm,
      method: "OPTIONS",
    });
    expect(result.success).toBe(false);
  });

  it("rejects timeout above 300", () => {
    const result = httpRequestNodeFormSchema.safeParse({
      ...validForm,
      timeout: 301,
    });
    expect(result.success).toBe(false);
  });

  it("rejects retries above 5", () => {
    const result = httpRequestNodeFormSchema.safeParse({
      ...validForm,
      retries: 6,
    });
    expect(result.success).toBe(false);
  });

  it("rejects an invalid node name", () => {
    const result = httpRequestNodeFormSchema.safeParse({
      ...validForm,
      name: "Fetch Users!",
    });
    expect(result.success).toBe(false);
  });

  it("applies defaults for optional fields", () => {
    const result = httpRequestNodeFormSchema.safeParse({
      name: "fetch_users",
      method: "GET",
      url: "https://api.example.com",
    });
    expect(result.success).toBe(true);
    expect(result.data.headers).toEqual({});
    expect(result.data.authType).toBe("none");
    expect(result.data.timeout).toBe(30);
    expect(result.data.retries).toBe(0);
  });
});

describe("getNodeFormSchema dispatch", () => {
  it("returns the http request schema for HTTP_REQUEST nodes", () => {
    expect(getNodeFormSchema(NODE_TYPES.HTTP_REQUEST)).toBe(
      httpRequestNodeFormSchema,
    );
  });
});

describe("getDefaultValues — http_request", () => {
  it("returns defaults for a fresh http_request node", () => {
    const values = getDefaultValues({
      type: NODE_TYPES.HTTP_REQUEST,
      data: { label: "http_1" },
    });
    expect(values.method).toBe("GET");
    expect(values.url).toBe("");
    expect(values.headers).toEqual({});
    expect(values.authType).toBe("none");
    expect(values.timeout).toBe(30);
    expect(values.retries).toBe(0);
  });

  it("hydrates form fields from stored config", () => {
    const values = getDefaultValues({
      type: NODE_TYPES.HTTP_REQUEST,
      data: {
        label: "fetch_users",
        config: {
          method: "POST",
          url: "https://api.example.com/users/{{user_id}}",
          headers: { "X-Api-Key": "{{api_key}}" },
          body: '{"name": "test"}',
          auth: { type: "bearer", token: "secret" },
          timeout: 15,
          retries: 2,
        },
      },
    });
    expect(values.method).toBe("POST");
    expect(values.url).toBe("https://api.example.com/users/{{user_id}}");
    expect(values.headers).toEqual({ "X-Api-Key": "{{api_key}}" });
    expect(values.body).toBe('{"name": "test"}');
    expect(values.authType).toBe("bearer");
    expect(values.authToken).toBe("secret");
    expect(values.timeout).toBe(15);
    expect(values.retries).toBe(2);
  });

  it("hydrates basic auth credentials", () => {
    const values = getDefaultValues({
      type: NODE_TYPES.HTTP_REQUEST,
      data: {
        label: "fetch_users",
        config: {
          auth: { type: "basic", username: "admin", password: "pw" },
        },
      },
    });
    expect(values.authType).toBe("basic");
    expect(values.authUsername).toBe("admin");
    expect(values.authPassword).toBe("pw");
  });
});
