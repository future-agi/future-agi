import React from "react";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../../mcp/testing/renderMcp";
import { APP_LOGOS } from "../app-logos.generated";
import AppLogo from "../components/AppLogo";

// An app's mark comes from the console's own assets only: the bundled logo
// for its catalog id first (its dark variant in dark mode when there is
// one), then a catalog icon that is itself a bundled asset, then the app's
// initials. No request to another site, ever.

const LOCAL_ICON = "/assets/icons/app/ic_github_grey.svg";
const REMOTE_ICON = {
  light: "https://icons.example.test/light.svg",
  dark: "https://icons.example.test/dark.svg",
};

const srcOf = (container) =>
  container.querySelector("img")?.getAttribute("src") ?? null;

describe("bundled app logos", () => {
  it("lists only root-relative console assets", () => {
    const entries = Object.entries(APP_LOGOS);
    expect(entries.length).toBeGreaterThan(0);
    for (const [id, logo] of entries) {
      expect(logo.light, id).toMatch(
        /^\/assets\/icons\/(?:apps|integrations)\/[a-z0-9-]+\.svg$/,
      );
      if (logo.dark) {
        expect(logo.dark, id).toMatch(
          /^\/assets\/icons\/apps\/[a-z0-9-]+-dark\.svg$/,
        );
      }
    }
  });

  it("shows the app's bundled logo, not its initials", () => {
    const { container } = renderMcp(
      <AppLogo app={{ id: "airtable", name: "Airtable" }} />,
    );
    expect(srcOf(container)).toBe(APP_LOGOS.airtable.light);
    expect(screen.queryByText("AI")).toBeNull();
  });

  it("shows the dark variant in dark mode when there is one", () => {
    expect(APP_LOGOS.github.dark).toBeTruthy();
    const { container } = renderMcp(
      <AppLogo app={{ id: "github", name: "GitHub" }} />,
      { mode: "dark" },
    );
    expect(srcOf(container)).toBe(APP_LOGOS.github.dark);
  });

  it("keeps the light logo in dark mode when there is no dark variant", () => {
    expect(APP_LOGOS.asana.dark).toBeUndefined();
    const { container } = renderMcp(
      <AppLogo app={{ id: "asana", name: "Asana" }} />,
      { mode: "dark" },
    );
    expect(srcOf(container)).toBe(APP_LOGOS.asana.light);
  });

  it.each(["light", "dark"])(
    "prefers the bundled logo over a remote catalog icon (%s)",
    (mode) => {
      const { container } = renderMcp(
        <AppLogo app={{ id: "notion", name: "Notion", icon: REMOTE_ICON }} />,
        { mode },
      );
      const logo = APP_LOGOS.notion;
      expect(srcOf(container)).toBe(
        mode === "dark" ? logo.dark || logo.light : logo.light,
      );
      expect(container.innerHTML).not.toContain("icons.example.test");
    },
  );

  it("prefers the bundled logo over a bundled catalog icon", () => {
    const { container } = renderMcp(
      <AppLogo
        app={{ id: "slack", name: "Slack", icon: { light: LOCAL_ICON } }}
      />,
    );
    expect(srcOf(container)).toBe(APP_LOGOS.slack.light);
  });

  it("uses a bundled catalog icon for an app with no bundled logo", () => {
    expect(APP_LOGOS["acme-docs"]).toBeUndefined();
    const { container } = renderMcp(
      <AppLogo
        app={{
          id: "acme-docs",
          name: "Acme Docs",
          icon: { light: LOCAL_ICON },
        }}
      />,
    );
    expect(srcOf(container)).toBe(LOCAL_ICON);
  });

  it.each(["light", "dark"])(
    "falls back to initials with no bundled logo and a remote icon (%s)",
    (mode) => {
      const { container } = renderMcp(
        <AppLogo
          app={{ id: "acme-docs", name: "Acme Docs", icon: REMOTE_ICON }}
        />,
        { mode },
      );
      expect(container.querySelector("img")).toBeNull();
      expect(screen.getByText("AD")).toBeInTheDocument();
    },
  );

  it.each(["constructor", "__proto__", "toString"])(
    "reads only the map's own entries (id %s)",
    (id) => {
      const { container } = renderMcp(
        <AppLogo app={{ id, name: "Demo App" }} />,
      );
      expect(container.querySelector("img")).toBeNull();
      expect(screen.getByText("DA")).toBeInTheDocument();
    },
  );
});
