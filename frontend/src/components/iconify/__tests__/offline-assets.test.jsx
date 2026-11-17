import React from "react";
import fs from "node:fs";
import process from "node:process";
import path from "node:path";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { Icon, ICONIFY_API_ENABLED } from "../offline";
import { registerOfflineIcon, registeredIconNames } from "../registry";
import bundled from "../offline-icons.json";
import { checkIconLicences } from "../../../../scripts/check-icon-licences.mjs";

const root = process.env.OFFLINE_ASSET_SOURCE_ROOT || process.cwd();
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");

// The source root can be the exact parent snapshot for red/green proofs.
describe("offline asset source contract", () => {
  it("collects literal maps and all computed names, with none missing offline", async () => {
    // Run the same live scan and assertions in native Node, without the DOM
    // worker's module-runner overhead. Await it so worker RPC stays responsive.
    // Keep the existing 30s test budget; bound and reap a stuck child within it.
    const { stdout } = await promisify(execFile)(
      process.execPath,
      [path.resolve("scripts/check-offline-icon-source.mjs"), root],
      { timeout: 25_000, killSignal: "SIGKILL" },
    );
    expect(JSON.parse(stdout).status).toBe("PASS");
  });

  it("ships real copyright notices, not placeholder licence templates", () => {
    const result = checkIconLicences(root);
    expect(result.errors).toEqual([]);
  });

  it("substitutes restricted artwork with the documented permissive glyphs", () => {
    const mapping = JSON.parse(read("scripts/offline-icon-aliases.json"));
    for (const [name, target] of Object.entries(
      mapping.redistributionReplacements,
    )) {
      expect(bundled[name], name).toEqual(bundled[target]);
    }
  });

  it("ships all 62 country images locally using the registered flag geometry", () => {
    const helper = read("src/components/agent-definitions/helper.jsx");
    expect(helper).not.toContain("flagcdn.com");
    const flags = [
      ...helper.matchAll(/\/assets\/country-flags\/([a-z]{2})\.svg/g),
    ];
    expect(flags).toHaveLength(62);
    for (const [, code] of flags) {
      const svg = read(`public/assets/country-flags/${code}.svg`);
      expect(svg).toContain(bundled[`circle-flags:${code}`].body);
      expect(svg).not.toMatch(/(?:href|src)=["']https?:/);
    }
  });

  it("disables the API for every direct and wrapped import", () => {
    expect(read("vite.config.js")).toContain(
      "src/components/iconify/offline.jsx",
    );
    expect(read("src/components/iconify/offline.jsx")).toContain(
      'from "@iconify/react/offline"',
    );
    expect(read("src/components/iconify/offline.jsx")).toContain(
      "ICONIFY_API_ENABLED = false",
    );
  });

  it("keeps the vendored variable font bytes identical to their pinned release", () => {
    const provenance = JSON.parse(
      read("src/assets/fonts/inter-provenance.json"),
    );
    expect(provenance.files).toHaveLength(7);
    for (const font of provenance.files) {
      const data = fs.readFileSync(
        path.join(root, "src/assets/fonts", font.file),
      );
      expect(`sha256:${createHash("sha256").update(data).digest("hex")}`).toBe(
        font.digest,
      );
    }
  });

  it("ships font-display swap and no external font links or imports", () => {
    for (const file of ["index.html", "src/global.css", "src/main.jsx"]) {
      expect(read(file)).not.toMatch(/fonts\.(googleapis|gstatic)\.com/);
    }
    const fonts = read("src/assets/fonts/fonts.css");
    expect(fonts).not.toMatch(/https?:\/\//);
    expect(fonts).not.toMatch(/\.woff\)/);
    expect(fonts.match(/font-display:\s*swap/g)?.length).toBeGreaterThan(20);
    expect(fonts).toContain("font-weight: 100 900"); // Includes real 450 and 650.
    for (const weight of [400, 500, 600, 700]) {
      expect(fonts).toContain(`font-weight: ${weight}`);
    }
  });
});

describe("offline rendering", () => {
  it("keeps data lazy and renders every original glyph without an API", () => {
    expect(ICONIFY_API_ENABLED).toBe(false);
    expect(registeredIconNames().length).toBeLessThan(
      Object.keys(bundled).length,
    );
    expect(read("src/components/iconify/offline.jsx")).not.toContain(
      "offline-icons.json",
    );
    for (const name of Object.keys(bundled)) {
      const html = renderToStaticMarkup(<Icon icon={bundled[name]} />);
      expect(html, name).toContain("<svg");
      expect(html, name).not.toContain("data-missing-icon");
      expect(bundled[name].body, name).not.toMatch(
        /<(?:script|foreignObject)|(?:href|src)=["']https?:/i,
      );
    }
  });

  it("preserves legacy names, classes and the onLoad callback", () => {
    registerOfflineIcon("gg:check-o", bundled["gg:check-o"]);
    const onLoad = vi.fn();
    const { container, rerender } = render(
      <Icon icon="gg-check-o" onLoad={onLoad} className="caller" />,
    );
    expect(container.querySelector("svg.caller.iconify--gg")).toBeTruthy();
    expect(container.querySelector("[data-missing-icon]")).toBeNull();
    expect(onLoad).toHaveBeenCalledExactlyOnceWith("gg-check-o");
    rerender(<Icon icon="gg-check-o" onLoad={onLoad} />);
    expect(onLoad).toHaveBeenCalledTimes(1);
  });

  it("shows a visible missing glyph instead of fetching an unknown name", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    render(<Icon icon="missing-test:no-icon" width={24} />);
    const fallback = screen.getByRole("img", {
      name: "Missing offline icon: missing-test:no-icon",
    });
    expect(fallback.tagName.toLowerCase()).toBe("svg");
    expect(fallback.querySelector("path")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
    fetch.mockRestore();
  });
});
