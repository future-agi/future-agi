import React from "react";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../../mcp/testing/renderMcp";
import ServerMark from "../../mcp/components/ServerMark";
import AppLogo from "./AppLogo";

const LOCAL_LOGO = "/assets/icons/app/ic_github_grey.svg";

describe("offline app logos", () => {
  it("uses initials for a recognized MCP app without a bundled logo", () => {
    const { container } = renderMcp(
      <ServerMark id="linear" url="https://mcp.linear.app/sse" />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("LI")).toBeInTheDocument();
  });

  it("keeps the bundled GitHub logo", () => {
    const { container } = renderMcp(
      <ServerMark id="github" url="https://api.githubcopilot.com/mcp" />,
    );
    expect(container.querySelector("img")).toHaveAttribute("src", LOCAL_LOGO);
  });

  it.each(["light", "dark"])(
    "does not mount a remote catalog image in %s mode",
    (mode) => {
      const { container } = renderMcp(
        <AppLogo
          app={{
            name: "Demo App",
            icon: {
              light: "https://icons.example.test/light.svg",
              dark: "https://icons.example.test/dark.svg",
            },
          }}
        />,
        { mode },
      );
      expect(container.querySelector("img")).toBeNull();
      expect(screen.getByText("DA")).toBeInTheDocument();
    },
  );

  it.each([
    "//icons.example.test/logo.svg",
    "/\\icons.example.test/logo.svg",
    "data:image/svg+xml,fixture",
    "/assets/\\icons.example.test/logo.svg",
  ])("falls back locally for unsupported catalog source %s", (src) => {
    const { container } = renderMcp(
      <AppLogo app={{ name: "Demo", icon: { light: src } }} />,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("accepts root-relative bundled catalog artwork", () => {
    const { container } = renderMcp(
      <AppLogo app={{ name: "GitHub", icon: { light: LOCAL_LOGO } }} />,
    );
    expect(container.querySelector("img")).toHaveAttribute("src", LOCAL_LOGO);
  });
});
