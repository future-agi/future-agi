import { describe, expect, it } from "vitest";
import { sourceLabel } from "../designReview.utils";
import { AGENT_TOOL_SOURCES, sourceWords } from "../designReview.constants";

describe("where a reviewed item came from", () => {
  it("says a hosted agent's tools come from its settings, not its code", () => {
    expect(sourceLabel("agent_settings")).toBe("From the agent's settings");
    expect(sourceWords(sourceLabel("agent_settings"))).toBe(
      "from the agent's settings",
    );
    expect(sourceLabel("agent_code")).toBe("From the agent's code");
  });

  it("treats a tool from the agent's settings as one the agent still calls", () => {
    expect(AGENT_TOOL_SOURCES.has("agent_settings")).toBe(true);
    expect(AGENT_TOOL_SOURCES.has("agent_code")).toBe(true);
    expect(AGENT_TOOL_SOURCES.has("your_change")).toBe(false);
  });
});
