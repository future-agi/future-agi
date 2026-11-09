import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";
import { clientInstalls } from "./clientInstalls";

describe("MCP client installs", () => {
  const endpoint = "https://org-gateway.example.test/mcp/v/review";
  it("covers the nine scoped client families without taking a credential", () => {
    const rows = clientInstalls(
      "https://org-gateway.example.test/v1",
      "review",
    );
    expect(rows).toHaveLength(9);
    for (const row of rows) {
      expect(row.endpoint).toBe(endpoint);
      if (row.format === "json")
        expect(() => JSON.parse(row.snippet)).not.toThrow();
      if (row.snippet) expect(row.snippet).toContain(endpoint);
      expect(row).not.toHaveProperty("key");
    }
  });
  it("uses each client's native JSON shape", () => {
    const rows = Object.fromEntries(
      clientInstalls("https://org-gateway.example.test/v1", "review").map(
        (row) => [row.id, row],
      ),
    );
    expect(JSON.parse(rows.cursor.snippet).mcpServers.futureagi.url).toBe(
      endpoint,
    );
    expect(JSON.parse(rows.vscode.snippet).servers.futureagi.type).toBe("http");
    expect(JSON.parse(rows.gemini.snippet).mcpServers.futureagi.httpUrl).toBe(
      endpoint,
    );
    expect(JSON.parse(rows.responses.snippet).tools[0].server_url).toBe(
      endpoint,
    );
    expect(rows.codex.snippet).toContain(
      'bearer_token_env_var = "FUTUREAGI_KEY"',
    );
    expect(rows["claude-code"].snippet).toContain(
      '"Authorization: Bearer $FUTUREAGI_KEY"',
    );
  });
  it("parses TOML and shell syntax with real parsers without running a client", () => {
    const rows = Object.fromEntries(
      clientInstalls("https://org-gateway.example.test/v1", "review").map(
        (row) => [row.id, row],
      ),
    );
    const parsed = execFileSync(
      "python3",
      [
        "-I",
        "-c",
        "import json,sys,tomllib; print(json.dumps(tomllib.loads(sys.stdin.read())))",
      ],
      { input: rows.codex.snippet, encoding: "utf8", timeout: 10000 },
    );
    expect(JSON.parse(parsed).mcp_servers.futureagi.url).toBe(endpoint);
    execFileSync("bash", ["-n"], {
      input: rows["claude-code"].snippet,
      timeout: 10000,
    });
    for (const row of Object.values(rows).filter(
      (item) => item.format === "url",
    )) {
      expect(new URL(row.snippet).href).toBe(endpoint);
    }
  });
  it("deep links decode to the same secret-free server config", () => {
    const rows = Object.fromEntries(
      clientInstalls("https://org-gateway.example.test/v1", "review").map(
        (row) => [row.id, row],
      ),
    );
    const cursor = new URL(rows.cursor.deepLink);
    expect(JSON.parse(atob(cursor.searchParams.get("config"))).url).toBe(
      endpoint,
    );
    const vscode = JSON.parse(
      decodeURIComponent(rows.vscode.deepLink.split("?")[1]),
    );
    expect(vscode.url).toBe(endpoint);
    expect(vscode.headers.Authorization).toBe("Bearer ${env:FUTUREAGI_KEY}");
  });
  it("rejects credentials, query strings and non-HTTP gateway URLs", () => {
    for (const url of [
      "ftp://example.test",
      "https://user:password@example.test",
      "https://example.test?key=private",
      "https://example.test/#private",
    ]) {
      expect(() => clientInstalls(url, "review")).toThrow();
    }
    expect(() => clientInstalls("https://example.test", "../other")).toThrow();
  });
});
