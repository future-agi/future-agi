import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import FullConfigTab from "../FullConfigTab";

// Gateway settings > Full config: the organization's gateway config as the
// gateway receives it, read-only. A section the console does not offer
// (MCP, agent-to-agent, tool policy) is shown when it holds something, and
// left out while it is empty. The config itself is not changed.

const shown = () =>
  JSON.parse(screen.getByTestId("full-config-json").textContent);

describe("Full config", () => {
  it("names the fields masked in the read-only configuration", () => {
    render(<FullConfigTab config={{}} />);
    expect(
      screen.getByText(
        "What this organization's gateway config holds, read-only. Stored secret slots, credential-named fields, header values and recognized URL credential fields are masked. Other text can contain credentials.",
      ),
    ).toBeInTheDocument();
  });

  it("leaves out empty MCP, agent-to-agent and tool policy sections", () => {
    const config = {
      providers: { openai: { models: ["gpt-4o-mini"] } },
      mcp: {},
      a2a: {},
      tool_policy: {},
      routing: {},
    };
    render(<FullConfigTab config={config} />);
    expect(shown()).toEqual({
      providers: { openai: { models: ["gpt-4o-mini"] } },
      routing: {},
    });
    expect(screen.getByTestId("full-config-json")).not.toHaveTextContent(
      /mcp|a2a|tool_policy/,
    );
    // The config handed to the view is unchanged.
    expect(config).toHaveProperty("mcp", {});
    expect(config).toHaveProperty("a2a", {});
    expect(config).toHaveProperty("tool_policy", {});
  });

  it("leaves out those sections when they are null or an empty list", () => {
    render(
      <FullConfigTab
        config={{ mcp: null, a2a: [], tool_policy: null, cache: {} }}
      />,
    );
    expect(shown()).toEqual({ cache: {} });
  });

  it("shows those sections when they hold something", () => {
    render(
      <FullConfigTab
        config={{
          mcp: { enabled: true },
          a2a: { agents: ["helper"] },
          tool_policy: { default_action: "allow" },
        }}
      />,
    );
    expect(shown()).toEqual({
      mcp: { enabled: true },
      a2a: { agents: ["helper"] },
      tool_policy: { default_action: "allow" },
    });
  });
});
