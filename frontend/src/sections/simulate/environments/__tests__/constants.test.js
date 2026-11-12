import { describe, it, expect } from "vitest";
import {
  OPTIONS,
  OPTION_STATUS,
  BRING_YOUR_AGENT_ORDER,
  DEFAULT_ENTRY_TAB,
} from "../environmentOptions";
import { CODE_UPLOAD_COPY } from "../codeUpload.constants";
import { ENTRY_AGENT_TYPES } from "../agentTypes";
import { HOSTED_PLATFORMS_BY_TYPE } from "../hostedPlatforms";
import { REPO_PROVIDERS } from "../repoProviders";
import { ENV_STATUS, STATUS_META } from "../myEnvironments.constants";

const liveIds = (arr) => arr.filter((o) => !o.comingSoon).map((o) => o.id);
const comingSoonIds = (arr) => arr.filter((o) => o.comingSoon).map((o) => o.id);

describe("environmentOptions", () => {
  it("omits the running and scratch options", () => {
    const ids = OPTIONS.map((o) => o.id);
    expect(ids).not.toContain("running");
    expect(ids).not.toContain("scratch");
  });

  it("gates ids by status", () => {
    const live = OPTIONS.filter((o) => o.status === OPTION_STATUS.LIVE).map(
      (o) => o.id,
    );
    const coming = OPTIONS.filter(
      (o) => o.status === OPTION_STATUS.COMING_SOON,
    ).map((o) => o.id);
    expect(live).toEqual(["source", "hosted", "upload"]);
    expect(coming).toEqual(["templates", "web", "mcp", "local"]);
  });

  it("orders bring-your-agent options and every id exists in OPTIONS", () => {
    expect(BRING_YOUR_AGENT_ORDER).toEqual([
      "source",
      "upload",
      "hosted",
      "mcp",
      "local",
    ]);
    const ids = OPTIONS.map((o) => o.id);
    BRING_YOUR_AGENT_ORDER.forEach((id) => expect(ids).toContain(id));
  });

  it("drops zip from upload preview and upload copy", () => {
    const upload = OPTIONS.find((o) => o.id === "upload");
    expect(upload.preview).not.toContain(".zip");
    Object.values(CODE_UPLOAD_COPY).forEach((copy) => {
      expect(String(copy).toLowerCase()).not.toContain("zip");
    });
  });

  it("defaults the entry tab to build", () => {
    expect(DEFAULT_ENTRY_TAB).toBe("build");
  });
});

describe("agentTypes", () => {
  it("gates the agent-type roster", () => {
    expect(liveIds(ENTRY_AGENT_TYPES)).toEqual(["voice", "text"]);
    // Modalities not built in this release stay listed, disabled.
    expect(comingSoonIds(ENTRY_AGENT_TYPES)).toEqual([
      "computer",
      "code",
      "robotics",
    ]);
  });
});

describe("repoProviders", () => {
  it("only github is live", () => {
    expect(liveIds(REPO_PROVIDERS)).toEqual(["github"]);
    expect(comingSoonIds(REPO_PROVIDERS)).toEqual(["gitlab", "bitbucket"]);
    // The Source repository card previews every host it lists.
    expect(OPTIONS.find((o) => o.id === "source").preview).toEqual([
      "GitHub",
      "GitLab",
      "Bitbucket",
    ]);
  });
});

describe("hostedPlatforms", () => {
  it("keeps only the voice and text rosters", () => {
    expect(Object.keys(HOSTED_PLATFORMS_BY_TYPE)).toEqual(["voice", "text"]);
    expect(HOSTED_PLATFORMS_BY_TYPE.voice.map((p) => p.id)).toEqual([
      "vapi",
      "retell",
      "other",
      "livekit",
    ]);
    // No coming-soon hosted voice platform is listed (Bland and ElevenLabs
    // are not connectors, so they are absent, not shown disabled).
    expect(comingSoonIds(HOSTED_PLATFORMS_BY_TYPE.voice)).toEqual([]);
    // Chat lists the real connector first, then the platforms that are not
    // connectors in this release, disabled.
    expect(HOSTED_PLATFORMS_BY_TYPE.text.map((p) => p.id)).toEqual([
      "retell_chat",
      "openai_assistants",
      "langgraph",
      "crewai",
      "claude_agents",
    ]);
    expect(liveIds(HOSTED_PLATFORMS_BY_TYPE.text)).toEqual(["retell_chat"]);
  });
});

describe("STATUS_META", () => {
  it("has a pill for every environment status", () => {
    Object.values(ENV_STATUS).forEach((status) =>
      expect(STATUS_META[status]).toBeDefined(),
    );
  });
});
