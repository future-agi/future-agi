import React from "react";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import {
  offlineIconPlugin,
  staticClosure,
} from "../../../../scripts/offline-icon-plugin.mjs";
import { backendIconConsumers } from "../../../../scripts/backend-icon-consumers.mjs";
import SkillPicker from "../../../sections/falcon-ai/components/SkillPicker";
import { hasRegisteredIcon } from "../registry";

const fixture = vi.hoisted(() => ({ skills: [] }));
vi.mock("../../../sections/falcon-ai/store/useFalconStore", () => ({
  default: (selector) =>
    selector({ skills: fixture.skills, setPendingPrompt: vi.fn() }),
}));
vi.mock("../../../sections/falcon-ai/components/SkillEditorDialog", () => ({
  default: () => null,
}));
const root = process.cwd();

it("registers every backend default through the real SkillPicker before first render", () => {
  fixture.skills = Object.keys(backendIconConsumers).map((icon, index) => ({
    id: index,
    icon,
    name: `Backend ${index}`,
    slug: `backend-${index}`,
  }));
  const { container } = render(<SkillPicker onSkillsChanged={() => {}} />);
  expect(container.querySelectorAll("svg.iconify")).toHaveLength(11); // 10 skills + create
  expect(container.querySelectorAll("[data-missing-icon]")).toHaveLength(0);
  for (const icon of Object.keys(backendIconConsumers))
    expect(hasRegisteredIcon(icon), icon).toBe(true);
});

it("renders an unmapped backend name as the existing visible fallback, never blank or fetched", () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  fixture.skills = [
    {
      id: "unmapped",
      name: "Unmapped",
      slug: "unmapped",
      icon: "unmapped-backend:missing",
    },
  ];
  const { container } = render(<SkillPicker onSkillsChanged={() => {}} />);
  const fallback = container.querySelector(
    '[data-missing-icon="unmapped-backend:missing"]',
  );
  expect(fallback?.tagName.toLowerCase()).toBe("svg");
  expect(fallback.querySelector("path")).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  fetch.mockRestore();
});

describe("module ownership", () => {
  it("covers every collected source and explicitly maps all backend defaults", async () => {
    // Keep every live-scan assertion in native Node, not the DOM module runner.
    // Await the bounded child so RPC stays responsive within the unchanged 30s.
    const { stdout } = await promisify(execFile)(
      process.execPath,
      [path.resolve("scripts/check-offline-icon-ownership.mjs"), root],
      { timeout: 25_000, killSignal: "SIGKILL" },
    );
    expect(JSON.parse(stdout).status).toBe("PASS");
  });

  it("uses static closures only, stopping at dynamic route boundaries", () => {
    const graph = {
      entry: { importedIds: ["shell"], dynamicallyImportedIds: ["route"] },
      shell: { importedIds: ["entry", "icon"] },
      route: { importedIds: ["lazy-icon"] },
    };
    expect([...staticClosure("entry", (id) => graph[id])].sort()).toEqual([
      "entry",
      "icon",
      "shell",
    ]);
  });

  it("injects synchronous deduplicated registration for dev, test and build consumers", () => {
    for (const mode of ["development", "test", "production"]) {
      const plugin = offlineIconPlugin();
      plugin.configResolved({
        root,
        mode,
        command: mode === "production" ? "build" : "serve",
      });
      plugin.buildStart();
      const file = "src/sections/falcon-ai/components/SkillPicker.jsx";
      const transformed = plugin.transform(
        fs.readFileSync(path.join(root, file), "utf8"),
        path.join(root, file),
      );
      expect(
        transformed.code.startsWith('import "virtual:offline-icons/consumer/'),
      ).toBe(true);
      const specifier = JSON.parse(
        transformed.code.split("\n")[0].slice(7, -1),
      );
      const resolved = plugin.resolveId(specifier);
      expect(resolved.moduleSideEffects).toBe(true);
      const registration = plugin.load(resolved.id);
      for (const name of Object.keys(backendIconConsumers))
        expect(registration).toContain(`icon/${name}`);
      expect(registration).not.toMatch(/await|import\(/);
      const first = JSON.parse(registration.split("\n")[0].slice(7, -1));
      const data = plugin.load(plugin.resolveId(first).id);
      expect(data).toContain("registerOfflineIcon(");
      expect(data).not.toContain("offline-icons.json");
    }
  });
});
