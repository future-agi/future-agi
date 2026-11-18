import { describe, it, expect } from "vitest";
import { getNodeConfig } from "../common";

describe("getNodeConfig", () => {
  it("returns prompt config for 'llm_prompt' type", () => {
    const config = getNodeConfig("llm_prompt");
    expect(config.iconSrc).toContain("ic_chat_single");
    expect(config.color).toBe("status.warning.fg");
  });

  it("returns agent config for 'agent' type", () => {
    const config = getNodeConfig("agent");
    expect(config.iconSrc).toContain("ic_agents");
    expect(config.color).toBe("text.secondary");
  });

  it("returns eval config for 'eval' type", () => {
    const config = getNodeConfig("eval");
    expect(config.iconSrc).toContain("ic_rounded_square");
    expect(config.color).toBe("status.success.fg");
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
