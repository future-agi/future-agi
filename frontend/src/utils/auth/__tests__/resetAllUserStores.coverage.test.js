import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect, beforeAll } from "vitest";

import { resetAllUserStores } from "../clearStores";

// Every module under src that builds a Zustand store. Listing them from the
// source tree (rather than by hand) is the point: a store added later is
// covered by this test without anyone remembering to add it here.
const SRC_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const STORE_WRAPPER = path.join(SRC_ROOT, "utils", "zustand.js");

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return entry.name === "__tests__" || entry.name === "node_modules"
        ? []
        : sourceFiles(full);
    }
    return /\.(jsx?|tsx?)$/.test(entry.name) && !/\.test\./.test(entry.name)
      ? [full]
      : [];
  });
}

const STORE_IMPORT =
  /import\s*\{[^}]*\bcreate\b[^}]*\}\s*from\s*["'](zustand|src\/utils\/zustand)["']/;

const storeModules = sourceFiles(SRC_ROOT).filter(
  (file) =>
    file !== STORE_WRAPPER && STORE_IMPORT.test(fs.readFileSync(file, "utf8")),
);

const isStore = (value) =>
  (typeof value === "function" || typeof value === "object") &&
  value !== null &&
  typeof value.getState === "function" &&
  typeof value.setState === "function" &&
  typeof value.getInitialState === "function";

function exportedStores(moduleExports) {
  return Object.entries(moduleExports).flatMap(([name, value]) => {
    if (isStore(value)) return [[name, value]];
    // Factories such as createGridSelectionStore() export `{ store, ... }`.
    if (value && typeof value === "object" && isStore(value.store)) {
      return [[`${name}.store`, value.store]];
    }
    return [];
  });
}

describe("resetAllUserStores covers every Zustand store", () => {
  const stores = [];

  beforeAll(async () => {
    for (const file of storeModules) {
      const rel = path.relative(SRC_ROOT, file).replace(/\\/g, "/");
      const mod = await import(/* @vite-ignore */ `/src/${rel}`);
      for (const [name, store] of exportedStores(mod)) {
        stores.push({ id: `${rel}#${name}`, store });
      }
    }
  });

  it("finds the store modules", () => {
    expect(storeModules.length).toBeGreaterThan(30);
    expect(stores.length).toBeGreaterThan(40);
  });

  it("leaves no previous user's state in any store after logout", () => {
    for (const { store } of stores) {
      store.setState({ __previousUserSentinel: "user-a-data" });
    }

    resetAllUserStores();

    const leaked = stores
      .filter(
        ({ store }) =>
          store.getState().__previousUserSentinel === "user-a-data",
      )
      .map(({ id }) => id);
    expect(leaked).toEqual([]);
  });
});

describe("resetAllUserStores clears persisted store data", () => {
  it("removes the simulate environments draft from sessionStorage", async () => {
    const { useEnvironmentsStore } = await import(
      "src/sections/simulate/environments/store/useEnvironmentsStore"
    );
    useEnvironmentsStore.setState({
      workspaceEnvs: { "env-a": { id: "env-a", name: "user A environment" } },
    });
    expect(sessionStorage.getItem("simulate-environments-draft")).toContain(
      "user A environment",
    );

    resetAllUserStores();

    expect(useEnvironmentsStore.getState().workspaceEnvs).toEqual(
      useEnvironmentsStore.getInitialState().workspaceEnvs,
    );
    expect(
      sessionStorage.getItem("simulate-environments-draft") ?? "",
    ).not.toContain("user A environment");
  });
});

describe("Zustand stores are created through src/utils/zustand", () => {
  it("no module imports create from zustand directly", () => {
    const direct = sourceFiles(SRC_ROOT)
      .filter((file) => file !== STORE_WRAPPER)
      .filter((file) =>
        /import\s*\{[^}]*\bcreate\b[^}]*\}\s*from\s*["']zustand["']/.test(
          fs.readFileSync(file, "utf8"),
        ),
      )
      .map((file) => path.relative(SRC_ROOT, file));
    // A store made with zustand's own create() is not reset on logout, so the
    // next account signed in on this tab would see its state.
    expect(direct).toEqual([]);
  });
});
