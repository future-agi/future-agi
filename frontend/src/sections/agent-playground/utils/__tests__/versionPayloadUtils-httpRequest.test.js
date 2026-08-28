import { describe, it, expect } from "vitest";
import { parseVersionResponse } from "../versionPayloadUtils";
import { API_NODE_TYPES, NODE_TYPES } from "../constants";

function createApiHttpNode(overrides = {}) {
  return {
    id: "http-1",
    type: API_NODE_TYPES.ATOMIC,
    name: "Fetch users",
    node_template_id: "tpl-http",
    node_template_name: "http_request",
    position: { x: 10, y: 20 },
    config: {
      method: "POST",
      url: "https://api.example.com/users/{{user_id}}",
      headers: { "X-Api-Key": "{{api_key}}" },
      body: '{"name": "{{name}}"}',
      auth: { type: "bearer", token: "{{token}}" },
      timeout: 15,
      retries: 2,
    },
    ports: [
      {
        id: "port-in-1",
        key: "user_id",
        display_name: "user_id",
        direction: "input",
        data_schema: { type: "string" },
        required: true,
      },
      {
        id: "port-out-1",
        key: "response",
        display_name: "response",
        direction: "output",
        data_schema: { type: "object" },
        required: true,
      },
    ],
    ...overrides,
  };
}

describe("parseVersionResponse — http_request nodes", () => {
  it("maps atomic nodes with node_template_name http_request to HTTP_REQUEST type", () => {
    const { nodes } = parseVersionResponse({ nodes: [createApiHttpNode()] });
    expect(nodes[0].type).toBe(NODE_TYPES.HTTP_REQUEST);
  });

  it("keeps atomic nodes without template name as LLM_PROMPT", () => {
    const apiNode = createApiHttpNode();
    delete apiNode.node_template_name;
    const { nodes } = parseVersionResponse({ nodes: [apiNode] });
    expect(nodes[0].type).toBe(NODE_TYPES.LLM_PROMPT);
  });

  it("carries raw config onto the node data", () => {
    const { nodes } = parseVersionResponse({ nodes: [createApiHttpNode()] });
    expect(nodes[0].data.config).toEqual({
      method: "POST",
      url: "https://api.example.com/users/{{user_id}}",
      headers: { "X-Api-Key": "{{api_key}}" },
      body: '{"name": "{{name}}"}',
      auth: { type: "bearer", token: "{{token}}" },
      timeout: 15,
      retries: 2,
    });
  });

  it("carries node_template_id and node_template_name", () => {
    const { nodes } = parseVersionResponse({ nodes: [createApiHttpNode()] });
    expect(nodes[0].data.node_template_id).toBe("tpl-http");
    expect(nodes[0].data.node_template_name).toBe("http_request");
  });

  it("defaults config to empty object when missing", () => {
    const apiNode = createApiHttpNode();
    delete apiNode.config;
    const { nodes } = parseVersionResponse({ nodes: [apiNode] });
    expect(nodes[0].data.config).toEqual({});
  });

  it("maps ports with direction and schema", () => {
    const { nodes } = parseVersionResponse({ nodes: [createApiHttpNode()] });
    const ports = nodes[0].data.ports;
    expect(ports).toHaveLength(2);
    expect(ports[0]).toMatchObject({
      id: "port-in-1",
      key: "user_id",
      direction: "input",
    });
    expect(ports[1]).toMatchObject({
      id: "port-out-1",
      key: "response",
      direction: "output",
    });
  });

  it("accepts camelCase API shape (nodeTemplateName)", () => {
    const apiNode = createApiHttpNode();
    apiNode.nodeTemplateName = "http_request";
    delete apiNode.node_template_name;
    const { nodes } = parseVersionResponse({ nodes: [apiNode] });
    expect(nodes[0].type).toBe(NODE_TYPES.HTTP_REQUEST);
  });
});
