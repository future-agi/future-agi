import { describe, expect, it, vi } from "vitest";

import { hubApi } from "../api/endpoints";
import {
  hubApiUrl,
  hubCommand,
  hubRegistryUrl,
  isHostedHub,
  shellQuote,
} from "../lib/registry";
import { cliCommands } from "../lib/useThis";

vi.mock("src/config-global", () => ({
  HOST_API: "https://registry.example.test/api/",
  CURRENT_ENVIRONMENT: "local",
}));

const HOSTED = "https://api.futureagi.com";
const SELF_HOSTED = "https://registry.example.test/api/";
const REGISTRY = "https://registry.example.test/api/omega-hub/v1";

// In-app Hub launch fixes, OWNER-RULINGS 2026-10-31: Sol P1/P2 and D11.
// All cases are local string operations; no registry or shell is contacted.
describe("Hub registry command builders", () => {
  it.each([HOSTED, `${HOSTED}/`, "https://api.futureagi.com:443/"])(
    "keeps commands short for the default hosted registry %s",
    (apiBase) => {
      expect(isHostedHub(apiBase)).toBe(true);
      expect(cliCommands("team/agent", "1.2.3", apiBase)).toEqual({
        add: "omega hub add hub:team/agent@1.2.3",
        run: "omega hub run hub:team/agent@1.2.3",
      });
      expect(hubCommand(["login", "--token-stdin"], apiBase)).toBe(
        "omega hub login --token-stdin",
      );
      expect(hubCommand(["publish", "./my-entry", "--wait"], apiBase)).toBe(
        "omega hub publish ./my-entry --wait",
      );
    },
  );

  it.each([
    "https://api-us.futureagi.com",
    "https://api-eu.futureagi.com/",
    "https://api-us.futureagi.com:443/",
    "https://dev.api.futureagi.com",
    "https://api.futureagi.com/proxy",
  ])(
    "uses hosted copy but an explicit non-default registry for %s",
    (apiBase) => {
      expect(isHostedHub(apiBase)).toBe(true);
      const registry = `${apiBase.replace(/:443(?=\/|$)/, "").replace(/\/+$/, "")}/omega-hub/v1`;
      expect(cliCommands("team/agent", "1.2.3", apiBase)).toEqual({
        add: `omega hub add hub:team/agent@1.2.3 --registry ${registry}`,
        run: `omega hub run hub:team/agent@1.2.3 --registry ${registry}`,
      });
      for (const args of [
        ["login"],
        ["login", "--token-stdin"],
        ["publish", "./my-entry", "--wait"],
        ["status"],
        ["new", "my-agent", "--from", "hub:team/agent@1.2.3"],
      ]) {
        expect(hubCommand(args, apiBase)).toBe(
          `omega hub ${args.join(" ")} --registry ${registry}`,
        );
      }
    },
  );

  it("pins add and run to the same selected version and install registry", () => {
    expect(cliCommands("team/agent", "0.9.0-rc.2", SELF_HOSTED)).toEqual({
      add: `omega hub add hub:team/agent@0.9.0-rc.2 --registry ${REGISTRY}`,
      run: `omega hub run hub:team/agent@0.9.0-rc.2 --registry ${REGISTRY}`,
    });
  });

  it("uses runtime-resolved HOST_API by default, including its path prefix", () => {
    expect(hubRegistryUrl()).toBe(REGISTRY);
    expect(cliCommands("team/agent", "1.0.0").run).toBe(
      `omega hub run hub:team/agent@1.0.0 --registry ${REGISTRY}`,
    );
  });

  it.each([
    ["login"],
    ["login", "--token-stdin"],
    ["publish", "./my-entry", "--wait"],
    ["status"],
    ["new", "my-agent", "--from", "hub:team/agent@1.2.3"],
  ])("routes %j to this install", (...args) => {
    expect(hubCommand(args, SELF_HOSTED)).toBe(
      `omega hub ${args.join(" ")} --registry ${REGISTRY}`,
    );
  });

  it.each([
    "http://api.futureagi.com",
    "https://api.futureagi.com.evil.test",
    "https://api-us.futureagi.com.evil.test",
    "https://api-eu.futureagi.com:8443",
    "http://localhost:8000",
  ])(
    "never mistakes a different registry for the hosted default: %s",
    (apiBase) => {
      expect(isHostedHub(apiBase)).toBe(false);
      expect(hubCommand(["login"], apiBase)).toContain(" --registry ");
    },
  );

  it("resolves relative and same-origin API bases without using the CLI default", () => {
    const origin = "https://app.example.test";
    expect(hubRegistryUrl("/api/", origin)).toBe(`${origin}/api/omega-hub/v1`);
    expect(hubRegistryUrl("", origin)).toBe(`${origin}/omega-hub/v1`);
  });

  it("quotes whitespace, quotes and shell substitutions as one literal argument", () => {
    expect(shellQuote("a'b")).toBe("'a'\"'\"'b'");
    expect(shellQuote('a "b" $(id); `id`')).toBe("'a \"b\" $(id); `id`'");
    expect(shellQuote("")).toBe("''");
    expect(
      hubCommand(["publish", "./my team's entry", "--wait"], SELF_HOSTED),
    ).toBe(
      `omega hub publish './my team'"'"'s entry' --wait --registry ${REGISTRY}`,
    );
    expect(cliCommands("team/a' b", "1.0.0", HOSTED).run).toBe(
      `omega hub run 'hub:team/a'"'"' b@1.0.0'`,
    );
  });

  it("quotes a configured registry path containing single quotes and metacharacters", () => {
    const apiBase = "https://registry.example.test/team's $(id)";
    expect(hubCommand(["login"], apiBase)).toBe(
      `omega hub login --registry 'https://registry.example.test/team'"'"'s%20$(id)/omega-hub/v1'`,
    );
  });

  it("does not manufacture an undefined version if a caller has no pin", () => {
    expect(cliCommands("team/agent", undefined, HOSTED)).toEqual({
      add: "omega hub add hub:team/agent",
      run: "omega hub run hub:team/agent",
    });
  });

  it("builds a version-specific bundle endpoint with encoded path segments", () => {
    const path = hubApi.versionBundle("my team", "a/b", "1.0.0+build");
    expect(path).toBe(
      "/omega-hub/v1/entries/my%20team/a%2Fb/versions/1.0.0%2Bbuild/bundle",
    );
    expect(hubApiUrl(path, SELF_HOSTED)).toBe(
      `https://registry.example.test/api${path}`,
    );
  });
});
