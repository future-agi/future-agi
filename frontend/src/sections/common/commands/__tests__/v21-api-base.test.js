// the capability contract(4) (ship-blocker, prototype dist v2.1): every command, snippet
// and generated file prints ONE API base, this build's HOST_API (the
// prototype's mock/configured host here), never the production host.
import { describe, expect, it, vi } from "vitest";
import { HOST_API } from "src/config-global";
import {
  FI_BASE_URL,
  fiGetRequest,
  fiRequest,
  requestSnippets,
} from "../requestSnippets";
import { FI_BASE_URL as LINES_BASE } from "src/sections/common/RunSources/snippets/lines";
import { buildUploadSnippets } from "src/sections/develop-detail/Experiment/uploadSnippets";
import { traceUploadScript } from "src/sections/common/RunSources/snippets/python";
import { traceUploadScriptTs } from "src/sections/common/RunSources/snippets/typescript";
import { harnessScript } from "src/sections/common/RunSources/snippets/harness";
import { catalogEntry } from "src/sections/common/RunSources/catalog";
import { defaultRecipe } from "src/sections/common/RunSources/recipe/recipeModel";
// 4984619941 retired the illustrative simulate bridge (including its Python
// sample). Cover the real Settings guide's copyable IDE configs instead.
import { buildMcpClientConfigs } from "src/sections/settings/mcp-server/clientSetupSnippets";
import { deploymentMcpUrl } from "src/utils/deploymentMcpUrl";
import { mcpUrlOf } from "src/sections/settings/mcp-server/mcpUrl";

// The prototype build's configured host (dataset integration VITE_HOST_API). vi.mock
// is hoisted above the imports, so the literal is repeated in the factory.
const PROTOTYPE_HOST = "http://127.0.0.1:8999";
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "http://127.0.0.1:8999",
}));

const IDS = {
  datasetId: "d",
  experimentId: "e",
  runId: "r",
  runLabel: "#1A",
};
const PROD = "api.futureagi.com";

/** Every snippet a user can copy, by surface. */
function everySnippet() {
  const post = requestSnippets(fiRequest("/model-hub/datasets/imports/", {}));
  const get = requestSnippets(
    fiGetRequest("/model-hub/datasets/d/results/", { page: 1 }),
  );
  const upload = buildUploadSnippets({ experimentId: "e", runId: "r" });
  const claude = catalogEntry("claude_code");
  const mcpConfigs = buildMcpClientConfigs(deploymentMcpUrl());
  return {
    "post.curl": post.curl,
    "post.python": post.python,
    "post.typescript": post.typescript,
    "get.curl": get.curl,
    ...Object.fromEntries(
      Object.entries(upload)
        .filter(([, v]) => typeof v === "string")
        .map(([k, v]) => [`upload.${k}`, v]),
    ),
    "runner.python": traceUploadScript({
      entry: catalogEntry("langgraph"),
      ids: IDS,
    }),
    "runner.typescript": traceUploadScriptTs({
      entry: catalogEntry("mastra"),
      ids: IDS,
    }),
    "runner.harness": harnessScript({
      entry: claude,
      ids: IDS,
      recipe: defaultRecipe(claude),
    }),
    ...Object.fromEntries(
      Object.entries(mcpConfigs).map(([k, v]) => [`settings.mcp.${k}`, v]),
    ),
    "settings.mcp.url": mcpUrlOf(undefined, HOST_API),
  };
}

describe("one API base from HOST_API", () => {
  it("the printed base is this build's HOST_API", () => {
    expect(HOST_API).toBe(PROTOTYPE_HOST);
    expect(FI_BASE_URL).toBe(HOST_API);
    expect(LINES_BASE).toBe(HOST_API);
    expect(fiRequest("/p/", {}).url).toBe(`${HOST_API}/p/`);
    expect(new URL(fiGetRequest("/p/", { a: 1 }).url).origin).toBe(HOST_API);
    expect(deploymentMcpUrl().startsWith(`${HOST_API}/`)).toBe(true);
    expect(deploymentMcpUrl()).toBe(`${HOST_API}/mcp`);
  });

  it("every command and generated file prints HOST_API", () => {
    const s = everySnippet();
    expect(s["post.curl"]).toContain(
      `"${HOST_API}/model-hub/datasets/imports/"`,
    );
    expect(s["upload.curl"]).toContain(HOST_API);
    expect(s["runner.python"]).toContain(HOST_API);
    expect(s["runner.typescript"]).toContain(HOST_API);
    expect(s["runner.harness"]).toContain(HOST_API);
    for (const client of [
      "cursor",
      "claudeCode",
      "vscode",
      "claudeDesktop",
      "windsurf",
    ]) {
      expect(s[`settings.mcp.${client}`]).toContain(`${HOST_API}/mcp`);
    }
  });

  it("no snippet contains the production host", () => {
    const hits = Object.entries(everySnippet())
      .filter(([, text]) => String(text ?? "").includes(PROD))
      .map(([k]) => k);
    expect(hits).toEqual([]);
    // No other production API or MCP host either (api-us, api-eu, mcp).
    const all = Object.values(everySnippet()).join("\n");
    expect(all).not.toMatch(/\b(?:api(?:-[a-z]+)?|mcp)\.futureagi\.com/);
  });
});
