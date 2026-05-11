import { describe, it, expect } from "vitest";
import { getNodeConfig } from "../common";
import { NODE_TYPES } from "../../../utils/constants";

describe("getNodeConfig", () => {
  it("returns prompt config for 'llm_prompt' type", () => {
    const config = getNodeConfig("llm_prompt");
    expect(config.iconSrc).toContain("ic_chat_single");
    expect(config.color).toBe("orange.500");
  });

  it("returns agent config for 'agent' type", () => {
    const config = getNodeConfig("agent");
    expect(config.iconSrc).toContain("ic_agents");
    expect(config.color).toBe("purple.500");
  });

  it("returns eval config for evaluation type", () => {
    const config = getNodeConfig(NODE_TYPES.EVAL);
    expect(config.iconSrc).toContain("ic_check");
    expect(config.color).toBe("green.600");
  });

  it("returns default config for unknown type", () => {
    const config = getNodeConfig("unknown_type");
    expect(config.color).toBe("text.secondary");
  });

  it("returns default config for undefined type", () => {
    const config = getNodeConfig(undefined);
    expect(config.color).toBe("text.secondary");
  });
});
