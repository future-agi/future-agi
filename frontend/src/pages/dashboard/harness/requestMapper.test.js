import { describe, expect, it } from "vitest";

import {
  buildJobPayload,
  deriveEgressDomains,
  MAX_SCENARIO_COUNT,
  mergeEgressDomains,
  parseEgressDomains,
  parseGitHubInput,
  unsupportedCredentialWarnings,
} from "./requestMapper";

describe("parseGitHubInput", () => {
  it("parses owner/repo shorthand", () => {
    expect(parseGitHubInput("future-agi/reference-agent")).toEqual({
      repository: "future-agi/reference-agent",
      ref: undefined,
    });
  });

  it("parses a full GitHub URL", () => {
    expect(
      parseGitHubInput("https://github.com/future-agi/reference-agent"),
    ).toEqual({
      repository: "future-agi/reference-agent",
      ref: undefined,
    });
  });

  it("parses a GitHub URL with /tree/branch", () => {
    expect(
      parseGitHubInput(
        "https://github.com/future-agi/reference-agent/tree/main",
      ),
    ).toEqual({
      repository: "future-agi/reference-agent",
      ref: "main",
    });
  });

  it("parses a GitHub URL with nested branch path", () => {
    expect(
      parseGitHubInput(
        "https://github.com/future-agi/ride-voice-agent/tree/codex/harden-voice-harness-flows",
      ),
    ).toEqual({
      repository: "future-agi/ride-voice-agent",
      ref: "codex/harden-voice-harness-flows",
    });
  });

  it("handles trailing slashes", () => {
    expect(
      parseGitHubInput("https://github.com/owner/repo/"),
    ).toEqual({
      repository: "owner/repo",
      ref: undefined,
    });
  });

  it("returns null for empty input", () => {
    expect(parseGitHubInput("")).toBeNull();
    expect(parseGitHubInput(null)).toBeNull();
    expect(parseGitHubInput(undefined)).toBeNull();
  });

  it("returns null for unparseable input", () => {
    expect(parseGitHubInput("not-a-url")).toBeNull();
    expect(parseGitHubInput("https://gitlab.com/owner/repo")).toBeNull();
  });
});

describe("deriveEgressDomains", () => {
  it("derives the exact hostname from a LiveKit signaling URL", () => {
    expect(
      deriveEgressDomains({
        LIVEKIT_URL: "wss://Project.LiveKit.Cloud/rtc",
      }),
    ).toEqual(["project.livekit.cloud"]);
  });

  it("rejects localhost, private, loopback, and link-local URL hosts", () => {
    expect(
      deriveEgressDomains({
        PUBLIC_URL: "https://api.example.com",
        LOCALHOST_URL: "http://localhost:8080",
        DOCKER_URL: "http://host.docker.internal:8080",
        LOOPBACK_URL: "http://127.0.0.1:8080",
        RFC1918_10_URL: "http://10.0.0.4",
        RFC1918_172_URL: "http://172.16.0.4",
        RFC1918_192_URL: "http://192.168.0.4",
        LINK_LOCAL_URL: "http://169.254.10.4",
        IPV6_LOOPBACK_URL: "http://[::1]",
        IPV6_LINK_LOCAL_URL: "http://[fe80::1]",
      }),
    ).toEqual(["api.example.com"]);
  });
});

describe("parseEgressDomains", () => {
  it("parses, normalizes, and exact-deduplicates comma/newline input", () => {
    expect(
      parseEgressDomains(
        " API.Example.com. ,\nturn.example.com\napi.example.com\n\n",
      ),
    ).toEqual(["api.example.com", "turn.example.com"]);
  });

  it("ignores schemes, paths, and malformed entries", () => {
    expect(
      parseEgressDomains(
        "https://api.example.com/path, turn.example.com/path, *.example.com, not a host, api.example.com",
      ),
    ).toEqual(["api.example.com"]);
  });
});

describe("mergeEgressDomains", () => {
  it("merges derived and explicit domains into a stable exact-host list", () => {
    expect(
      mergeEgressDomains(
        ["media.example.com", "API.Example.com"],
        parseEgressDomains("api.example.com\nturn.example.com."),
      ),
    ).toEqual(["api.example.com", "media.example.com", "turn.example.com"]);
  });
});

describe("MAX_SCENARIO_COUNT", () => {
  it("is 200", () => {
    expect(MAX_SCENARIO_COUNT).toBe(200);
  });
});

describe("buildJobPayload", () => {
  const baseState = {
    sourceMode: "upload",
    uploadedSource: { source_id: "abc-123" },
    githubRepository: "",
    githubVisibility: "public",
    githubInstallationId: "",
    scenarioCount: 5,
    configurationValues: {},
    secretFileRefs: {},
  };

  it("produces a valid v1.6 archive source payload", () => {
    const payload = buildJobPayload(baseState);
    expect(payload).toEqual({
      schema_version: "futureagi.harness-job.v1",
      source: {
        kind: "archive",
        archive_artifact_id: "abc-123",
        visibility: "public",
      },
      agent: {
        connector: "auto",
        config: {},
        secret_refs: {},
      },
      scenario_count: 5,
      artifacts: { level: "traces-and-recordings" },
      metadata: {},
    });
  });

  it("produces a valid v1.6 github source payload with ref extraction", () => {
    const payload = buildJobPayload({
      ...baseState,
      sourceMode: "github",
      githubRepository: "https://github.com/future-agi/ride-voice-agent/tree/main",
      githubVisibility: "public",
    });
    expect(payload.source).toEqual({
      kind: "github",
      repository: "future-agi/ride-voice-agent",
      ref: "main",
      visibility: "public",
    });
  });

  it("includes installation_id for private repos", () => {
    const payload = buildJobPayload({
      ...baseState,
      sourceMode: "github",
      githubRepository: "owner/repo",
      githubVisibility: "private",
      githubInstallationId: "12345",
    });
    expect(payload.source).toEqual({
      kind: "github",
      repository: "owner/repo",
      visibility: "private",
      installation_id: "12345",
    });
  });

  it("clamps scenario_count to MAX_SCENARIO_COUNT", () => {
    expect(buildJobPayload({ ...baseState, scenarioCount: 201 }).scenario_count).toBe(200);
    expect(buildJobPayload({ ...baseState, scenarioCount: 200 }).scenario_count).toBe(200);
    expect(buildJobPayload({ ...baseState, scenarioCount: 0 }).scenario_count).toBe(1);
    expect(buildJobPayload({ ...baseState, scenarioCount: -5 }).scenario_count).toBe(1);
  });

  it("floors fractional scenario counts", () => {
    expect(buildJobPayload({ ...baseState, scenarioCount: 3.7 }).scenario_count).toBe(3);
  });

  it("forwards configuration values as agent.config", () => {
    const payload = buildJobPayload({
      ...baseState,
      configurationValues: { REGION: "us-east-1" },
    });
    expect(payload.agent.config).toEqual({ REGION: "us-east-1" });
  });

  it("only includes platform-vault target_provider secret_refs", () => {
    const payload = buildJobPayload({
      ...baseState,
      secretFileRefs: {
        GOOD: { manager: "platform-vault", key: "k1", purpose: "target_provider" },
        BAD_MANAGER: { manager: "harness_environment_file", key: "k2", purpose: "target_provider" },
        BAD_PURPOSE: { manager: "platform-vault", key: "k3", purpose: "source_checkout" },
      },
    });
    expect(payload.agent.secret_refs).toEqual({
      GOOD: { manager: "platform-vault", key: "k1", purpose: "target_provider" },
    });
  });

  it("never includes environment_values in the payload", () => {
    const payload = buildJobPayload(baseState);
    expect(payload).not.toHaveProperty("environment_values");
    expect(JSON.stringify(payload)).not.toContain("environment_values");
  });
});

describe("unsupportedCredentialWarnings", () => {
  it("returns empty for no credentials", () => {
    expect(
      unsupportedCredentialWarnings({ environmentValues: {}, secretFileRefs: {} }),
    ).toEqual([]);
  });

  it("warns about pasted environment values", () => {
    const warnings = unsupportedCredentialWarnings({
      environmentValues: { OPENAI_API_KEY: "sk-test", REGION: "us-east-1" },
      secretFileRefs: {},
    });
    expect(warnings).toHaveLength(1);
    expect(warnings[0].names).toContain("OPENAI_API_KEY");
    expect(warnings[0].names).toContain("REGION");
    expect(warnings[0].reason).toMatch(/vault/i);
  });

  it("ignores empty environment values", () => {
    expect(
      unsupportedCredentialWarnings({
        environmentValues: { EMPTY: "", BLANK: "  " },
        secretFileRefs: {},
      }),
    ).toEqual([]);
  });

  it("warns about harness_environment_file refs", () => {
    const warnings = unsupportedCredentialWarnings({
      environmentValues: {},
      secretFileRefs: {
        GOOGLE_APPLICATION_CREDENTIALS: {
          manager: "harness_environment_file",
          key: "file-id",
          purpose: "target_provider",
        },
      },
    });
    expect(warnings).toHaveLength(1);
    expect(warnings[0].names).toContain("GOOGLE_APPLICATION_CREDENTIALS");
  });

  it("does not warn about platform-vault refs", () => {
    expect(
      unsupportedCredentialWarnings({
        environmentValues: {},
        secretFileRefs: {
          VAULT_SECRET: { manager: "platform-vault", key: "k1", purpose: "target_provider" },
        },
      }),
    ).toEqual([]);
  });
});
