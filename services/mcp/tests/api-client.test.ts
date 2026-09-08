import { describe, expect, it, vi } from "vitest";

import { executeApiTool } from "../src/api-client.js";
import { generatedTools } from "../src/generated/tools.generated.js";
import type { ApiCredentials } from "../src/types.js";

const credentials: ApiCredentials = { apiKey: "public", secretKey: "secret" };

describe("generated API tool executor", () => {
  it("maps generated query arguments and forwards credentials", async () => {
    const tool = generatedTools.find((item) => item.name === "list_datasets")!;
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ results: [] }),
    );

    await executeApiTool(
      tool,
      { search_text: "billing", page_size: 5 },
      credentials,
      {
        baseUrl: "https://api.example.test",
        timeoutMs: 1_000,
        maxResponseBytes: 10_000,
        fetchImplementation,
      },
    );

    const [url, request] = fetchImplementation.mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "https://api.example.test/model-hub/develops/get-datasets/?search_text=billing&page_size=5",
    );
    expect(new Headers(request?.headers).get("X-Api-Key")).toBe("public");
    expect(request?.method).toBe("GET");
  });

  it("rejects an oversized response", async () => {
    const tool = generatedTools.find((item) => item.name === "list_datasets")!;
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("x".repeat(101), {
        headers: { "Content-Length": "101" },
      }),
    );

    await expect(
      executeApiTool(tool, {}, credentials, {
        baseUrl: "https://api.example.test",
        timeoutMs: 1_000,
        maxResponseBytes: 100,
        fetchImplementation,
      }),
    ).rejects.toMatchObject({ status: 413 });
  });
});
