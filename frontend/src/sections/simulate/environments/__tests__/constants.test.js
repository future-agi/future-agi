import { describe, it, expect } from "vitest";
import { DEFAULT_ENTRY_TAB } from "../environmentOptions";
import { CODE_UPLOAD_COPY } from "../codeUpload.constants";
import { ENTRY_AGENT_TYPES } from "../agentTypes";
import { HOSTED_PLATFORMS_BY_TYPE } from "../hostedPlatforms";
import { REPO_PROVIDERS } from "../repoProviders";
import { ENV_STATUS, STATUS_META } from "../myEnvironments.constants";

const liveIds = (arr) => arr.filter((o) => !o.comingSoon).map((o) => o.id);
const comingSoonIds = (arr) => arr.filter((o) => o.comingSoon).map((o) => o.id);

describe("environmentOptions", () => {
  it("drops zip from the upload copy", () => {
    Object.values(CODE_UPLOAD_COPY).forEach((copy) => {
      expect(String(copy).toLowerCase()).not.toContain("zip");
    });
  });

  it("defaults the entry tab to My Environments", () => {
    expect(DEFAULT_ENTRY_TAB).toBe("my-environments");
  });
});

describe("agentTypes", () => {
  it("gates the agent-type roster", () => {
    expect(liveIds(ENTRY_AGENT_TYPES)).toEqual(["voice", "text"]);
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
  });
});

describe("hostedPlatforms", () => {
  it("keeps only the voice and text rosters", () => {
    expect(Object.keys(HOSTED_PLATFORMS_BY_TYPE)).toEqual(["voice", "text"]);
    expect(HOSTED_PLATFORMS_BY_TYPE.voice.map((p) => p.id)).toEqual([
      "vapi",
      "retell",
      "other",
      "bland",
      "elevenlabs",
      "livekit",
    ]);
    expect(HOSTED_PLATFORMS_BY_TYPE.text.map((p) => p.id)).toEqual([
      "retell_chat",
      "openai_assistants",
      "langgraph",
      "crewai",
      "claude_agents",
    ]);
  });
});

describe("STATUS_META", () => {
  it("has a pill for every environment status", () => {
    Object.values(ENV_STATUS).forEach((status) =>
      expect(STATUS_META[status]).toBeDefined(),
    );
  });
});
