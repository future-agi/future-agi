import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadTokensForApiJourney } from "./api-client.mjs";

const API_BASE = "http://localhost:8003";

describe("loadTokensForApiJourney", () => {
  const tempDirs = [];

  afterEach(async () => {
    vi.restoreAllMocks();
    for (const dir of tempDirs.splice(0)) {
      await fs.rm(dir, { force: true, recursive: true });
    }
  });

  it("reuses an accepted token file without calling the password login endpoint", async () => {
    const tokenFile = await createTokenFile({
      access: "cached-access",
      refresh: "cached-refresh",
      api_base: API_BASE,
    });
    const fetchImpl = vi.fn(async (url) => {
      expect(String(url)).toBe(`${API_BASE}/accounts/user-info/`);
      return jsonResponse({ status: true, id: "user-1" });
    });

    const tokens = await loadTokensForApiJourney(API_BASE, {
      env: {
        FUTURE_AGI_TOKEN_FILE: tokenFile,
        FUTURE_AGI_EMAIL: "local@example.com",
        FUTURE_AGI_PASSWORD: "unused",
      },
      fetchImpl,
    });

    expect(tokens).toMatchObject({
      access: "cached-access",
      refresh: "cached-refresh",
      source: "token_file",
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("falls back to password login and rewrites the token file when the cached token is rejected", async () => {
    const tokenFile = await createTokenFile({
      access: "expired-access",
      refresh: "expired-refresh",
      api_base: API_BASE,
    });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({ access: "fresh-access", refresh: "fresh-refresh" }),
      );

    const tokens = await loadTokensForApiJourney(API_BASE, {
      env: {
        FUTURE_AGI_TOKEN_FILE: tokenFile,
        FUTURE_AGI_EMAIL: "local@example.com",
        FUTURE_AGI_PASSWORD: "password",
      },
      fetchImpl,
    });
    const rewritten = JSON.parse(await fs.readFile(tokenFile, "utf8"));

    expect(tokens).toMatchObject({
      access: "fresh-access",
      refresh: "fresh-refresh",
      source: "login",
    });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(String(fetchImpl.mock.calls[1][0])).toBe(
      `${API_BASE}/accounts/token/`,
    );
    expect(rewritten).toMatchObject({
      access: "fresh-access",
      refresh: "fresh-refresh",
      api_base: API_BASE,
    });
  });

  it("does not use a token file written for a different API base", async () => {
    const tokenFile = await createTokenFile({
      access: "other-api-access",
      api_base: "http://localhost:9999",
    });
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ access: "fresh-access", refresh: "" }),
    );

    const tokens = await loadTokensForApiJourney(API_BASE, {
      env: {
        FUTURE_AGI_TOKEN_FILE: tokenFile,
        FUTURE_AGI_EMAIL: "local@example.com",
        FUTURE_AGI_PASSWORD: "password",
      },
      fetchImpl,
    });

    expect(tokens.access).toBe("fresh-access");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(String(fetchImpl.mock.calls[0][0])).toBe(
      `${API_BASE}/accounts/token/`,
    );
  });

  async function createTokenFile(payload) {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "api-token-cache-"));
    tempDirs.push(dir);
    const tokenFile = path.join(dir, "tokens.json");
    await fs.writeFile(tokenFile, `${JSON.stringify(payload)}\n`);
    return tokenFile;
  }
});

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    text: async () => JSON.stringify(body),
  };
}
