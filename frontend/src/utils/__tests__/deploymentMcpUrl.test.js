import { describe, expect, it } from "vitest";

import { HOST_API } from "src/config-global";
import { deploymentMcpUrl } from "../deploymentMcpUrl";

describe("deploymentMcpUrl", () => {
  it("is the configured API base plus /mcp", () => {
    expect(deploymentMcpUrl("http://localhost:8000")).toBe(
      "http://localhost:8000/mcp",
    );
    expect(deploymentMcpUrl("https://fi.selfhosted.example/api/")).toBe(
      "https://fi.selfhosted.example/api/mcp",
    );
    expect(deploymentMcpUrl("  https://fi.example//  ")).toBe(
      "https://fi.example/mcp",
    );
  });

  it("is null when no API base is configured", () => {
    expect(deploymentMcpUrl("")).toBeNull();
    expect(deploymentMcpUrl(null)).toBeNull();
  });

  it("defaults to the app's own API base from config", () => {
    expect(deploymentMcpUrl()).toBe(`${HOST_API.replace(/\/+$/, "")}/mcp`);
  });
});
