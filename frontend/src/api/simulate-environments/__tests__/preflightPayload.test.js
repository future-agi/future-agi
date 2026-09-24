import { describe, it, expect } from "vitest";
import {
  PREFLIGHT_CONNECTOR,
  PROVIDER_TO_CONNECTOR,
  environmentNameFor,
  draftToPreflightPayload,
} from "../preflightPayload";

const repoDraft = (over = {}) => ({
  kind: "repo",
  provider: "github",
  value: "acme/support-bot",
  ref: "main",
  visibility: "public",
  installationId: null,
  egress: null,
  secretFiles: [],
  ...over,
});

describe("PREFLIGHT_CONNECTOR / PROVIDER_TO_CONNECTOR", () => {
  it("exposes the HarnessAgent connector enum", () => {
    expect(PREFLIGHT_CONNECTOR).toEqual({
      AUTO: "auto",
      VAPI: "vapi",
      RETELL: "retell",
      RETELL_CHAT: "retell_chat",
      LIVEKIT: "livekit",
    });
  });

  it("maps only the reachable Phase-1 providers to a connector", () => {
    expect(PROVIDER_TO_CONNECTOR).toEqual({
      vapi: "vapi",
      retell: "retell",
      livekit: "livekit",
    });
  });
});

describe("draftToPreflightPayload — repo", () => {
  it("carries the exchanged secret refs from a pasted .env", () => {
    const { payload } = draftToPreflightPayload(
      repoDraft({ secret_refs: { OPENAI_API_KEY: "sref-3" } }),
    );
    expect(payload.agent.secret_refs).toStrictEqual({ OPENAI_API_KEY: "sref-3" });
  });

  it("maps the canonical owner/repo draft to the §5.1 example", () => {
    const { payload, skipped } = draftToPreflightPayload(repoDraft());
    expect(skipped).toBeUndefined();
    expect(payload).toStrictEqual({
      schema_version: "futureagi.harness-job.v1",
      source: {
        kind: "github",
        repository: "acme/support-bot",
        ref: "main",
        visibility: "public",
      },
      agent: { connector: "auto", config: {}, secret_refs: {} },
      scenario_count: 10,
      artifacts: {
        level: "full",
        retention_days: 30,
        allow_bundle_download: false,
        max_artifact_bytes: 1073741824,
      },
      metadata: { name: "support-bot", authoring_key: "support-bot" },
    });
  });

  it("lets a pasted tree URL ref win over the panel's default main", () => {
    const { payload } = draftToPreflightPayload(
      repoDraft({ value: "https://github.com/acme/support-bot/tree/release", ref: "main" }),
    );
    expect(payload.source.repository).toBe("acme/support-bot");
    expect(payload.source.ref).toBe("release");
  });

  it("lets an explicit typed branch win over the URL ref", () => {
    const { payload } = draftToPreflightPayload(
      repoDraft({ value: "https://github.com/acme/support-bot/tree/release", ref: "hotfix" }),
    );
    expect(payload.source.ref).toBe("hotfix");
  });

  it("omits ref when both the URL and draft ref are empty", () => {
    const { payload } = draftToPreflightPayload(repoDraft({ value: "acme/support-bot", ref: "" }));
    expect("ref" in payload.source).toBe(false);
  });

  it("carries installation_id only for a private repo", () => {
    const priv = draftToPreflightPayload(
      repoDraft({ visibility: "private", installationId: "123" }),
    ).payload;
    expect(priv.source.visibility).toBe("private");
    expect(priv.source.installation_id).toBe("123");

    const pub = draftToPreflightPayload(
      repoDraft({ visibility: "public", installationId: "123" }),
    ).payload;
    expect("installation_id" in pub.source).toBe(false);
  });

  it("adds security only when egress yields at least one public domain", () => {
    const { payload } = draftToPreflightPayload(
      repoDraft({ egress: "api.acme.com, localhost" }),
    );
    expect(payload.security).toStrictEqual({
      untrusted_source: true,
      read_only_source: true,
      allow_privileged: false,
      allow_host_runtime_control: false,
      allowed_egress_domains: ["api.acme.com"],
    });
  });

  it("never emits security for an empty egress", () => {
    const { payload } = draftToPreflightPayload(repoDraft());
    expect("security" in payload).toBe(false);
  });

  it("skips an unparseable repo string", () => {
    const { payload, skipped } = draftToPreflightPayload(repoDraft({ value: "not a repo" }));
    expect(payload).toBeUndefined();
    expect(skipped).toBe("Repository must be owner/repo");
  });
});

describe("draftToPreflightPayload — platform", () => {
  const platformDraft = (over = {}) => ({
    kind: "platform",
    agentType: "voice",
    provider: "vapi",
    agentId: "asst_1",
    ...over,
  });

  it("maps vapi to assistant_id and drops source", () => {
    const { payload } = draftToPreflightPayload(platformDraft());
    expect(payload.source).toBeUndefined();
    expect(payload.agent).toStrictEqual({
      connector: "vapi",
      mode: "connect_only",
      config: { assistant_id: "asst_1" },
      secret_refs: {},
    });
    expect(payload.metadata.name).toBe("asst_1");
  });

  it("carries the exchanged secret refs so the backend can resolve the key", () => {
    const { payload } = draftToPreflightPayload(
      platformDraft({ secret_refs: { VAPI_API_KEY: "sref-1" } }),
    );
    expect(payload.agent.secret_refs).toStrictEqual({ VAPI_API_KEY: "sref-1" });
  });

  it("maps retell and livekit to agent_id", () => {
    const retell = draftToPreflightPayload(platformDraft({ provider: "retell" })).payload;
    expect(retell.agent.connector).toBe("retell");
    expect(retell.agent.config).toStrictEqual({ agent_id: "asst_1" });

    const livekit = draftToPreflightPayload(platformDraft({ provider: "livekit" })).payload;
    expect(livekit.agent.connector).toBe("livekit");
    expect(livekit.agent.config).toStrictEqual({ agent_id: "asst_1" });
  });

  it("skips a provider outside the connector enum", () => {
    const { payload, skipped } = draftToPreflightPayload(platformDraft({ provider: "bland" }));
    expect(payload).toBeUndefined();
    expect(skipped).toMatch(/bland/);
  });
});

describe("draftToPreflightPayload — upload", () => {
  it("maps an uploaded archive to source.kind archive", () => {
    const { payload, skipped } = draftToPreflightPayload({
      kind: "upload",
      archive_artifact_id: "art_1",
      entry: "agent.py",
      files: [{ name: "agent.py" }],
    });
    expect(skipped).toBeUndefined();
    expect(payload.source).toStrictEqual({ kind: "archive", archive_artifact_id: "art_1" });
    expect(payload.agent).toStrictEqual({ connector: "auto", config: {}, secret_refs: {} });
  });

  it("carries the exchanged secret refs for an uploaded archive too", () => {
    const { payload } = draftToPreflightPayload({
      kind: "upload",
      archive_artifact_id: "art_1",
      entry: "agent.py",
      files: [{ name: "agent.py" }],
      secret_refs: { OPENAI_API_KEY: "sref-2" },
    });
    expect(payload.agent.secret_refs).toStrictEqual({ OPENAI_API_KEY: "sref-2" });
  });

  it("skips an upload draft without an archive id", () => {
    const { payload, skipped } = draftToPreflightPayload({
      kind: "upload",
      files: [{ name: "agent.py" }],
    });
    expect(payload).toBeUndefined();
    expect(skipped).toBe("Code upload preflight needs the uploaded archive");
  });
});

describe("environmentNameFor", () => {
  it("names a repo by its last path segment", () => {
    expect(environmentNameFor(repoDraft())).toBe("support-bot");
    expect(
      environmentNameFor(repoDraft({ value: "https://github.com/acme/support-bot/tree/release" })),
    ).toBe("support-bot");
  });

  it("names a platform draft by its agent id", () => {
    expect(environmentNameFor({ kind: "platform", provider: "vapi", agentId: "asst_1" })).toBe(
      "asst_1",
    );
  });

  it("names an upload draft by entry then first file", () => {
    expect(environmentNameFor({ kind: "upload", entry: "agent.py", files: [] })).toBe("agent.py");
    expect(environmentNameFor({ kind: "upload", entry: "", files: [{ name: "main.py" }] })).toBe(
      "main.py",
    );
  });

  it("falls back to agent", () => {
    expect(environmentNameFor(null)).toBe("agent");
    expect(environmentNameFor({ kind: "mystery" })).toBe("agent");
  });
});
