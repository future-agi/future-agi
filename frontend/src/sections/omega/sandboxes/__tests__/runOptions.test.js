import { describe, expect, it } from "vitest";

import {
  capReason,
  createRefusalText,
  customerSentences,
  templateReason,
} from "../lib/offerCopy";
import {
  cwdError,
  envErrors,
  folderLookup,
  folderMissing,
  formatByteCap,
  initialRunOptions,
  normalizeCwd,
  optionsSummary,
  runPayload,
  timeoutChoices,
  validateRunOptions,
} from "../lib/runOptions";
import { CAPS } from "./helpers";

const row = (key, value = "", id = key || "empty") => ({ id, key, value });

describe("Run options (unit)", () => {
  it("offers time limits up to EE's maximum, starting at its default", () => {
    expect(initialRunOptions(CAPS)).toEqual({
      timeoutSeconds: 60,
      cwd: "",
      env: [],
    });
    expect(timeoutChoices(CAPS)).toEqual([30, 60, 120, 300, 600, 900]);
    expect(
      timeoutChoices({
        exec: { defaultTimeoutSeconds: 45, maxTimeoutSeconds: 200 },
      }),
    ).toEqual([30, 45, 60, 120, 200]);
    // An EE that states no exec limits: no control, and nothing is sent.
    expect(timeoutChoices({})).toEqual([]);
    expect(initialRunOptions(undefined).timeoutSeconds).toBeNull();
    expect(runPayload(initialRunOptions(undefined), undefined)).toEqual({});
  });

  it("takes a folder relative to /workspace and refuses what EE refuses", () => {
    expect(normalizeCwd("/workspace/src/app/")).toBe("src/app");
    expect(normalizeCwd("./src")).toBe("src");
    expect(normalizeCwd("/workspace")).toBe("");
    expect(cwdError("src/app")).toBeNull();
    expect(cwdError("/workspace/src")).toBeNull();
    expect(cwdError("../etc")).toMatch(/inside \/workspace/);
    expect(cwdError("/workspace/../etc")).toMatch(/inside \/workspace/);
    expect(cwdError("src/./app")).toMatch(/without \. or \.\./);
    expect(cwdError("/etc")).toBe("Use a folder inside /workspace");
    expect(cwdError("src\\app")).toBe("Use / between folders");
    expect(cwdError("a".repeat(4097))).toBe("At most 4096 characters");
  });

  it("checks variables the way EE does: names, OMEGA_*, duplicates, value size", () => {
    expect(
      envErrors([
        row("GOOD_1", "x"),
        row("1BAD"),
        row("BAD-KEY"),
        row("omega_exec_id", "x"),
        row("GOOD_1", "y", "dup"),
        row("", "orphan value", "noname"),
        row("BIG", "x".repeat(8193)),
        row("", "", "blank"),
      ]),
    ).toEqual({
      "1BAD": { key: "Letters, digits and _, not starting with a digit" },
      "BAD-KEY": { key: "Letters, digits and _, not starting with a digit" },
      omega_exec_id: { key: "OMEGA_ names are set by the sandbox itself" },
      dup: { key: "Already set above" },
      noname: { key: "Name this variable" },
      BIG: { value: "At most 8192 bytes" },
    });
    // 8192 bytes counted as UTF-8, as EE counts them (4 x 2048 = 8192 is fine).
    expect(envErrors([row("UTF", "é".repeat(4096))])).toEqual({});
    expect(envErrors([row("UTF", "é".repeat(4097))])).toEqual({
      UTF: { value: "At most 8192 bytes" },
    });
    const many = Array.from({ length: 33 }, (_, i) => row(`V${i}`, "x"));
    expect(
      validateRunOptions({ timeoutSeconds: 60, cwd: "", env: many }, CAPS)
        .envCount,
    ).toBe("At most 32 variables");
  });

  it("sends only the keys EE accepts, and the time limit it shows", () => {
    const options = {
      timeoutSeconds: 600,
      cwd: "/workspace/build/",
      env: [row("NODE_ENV", "test"), row("", "", "blank"), row("EMPTY", "")],
    };
    expect(runPayload(options, CAPS)).toEqual({
      cwd: "build",
      env: { NODE_ENV: "test", EMPTY: "" },
      timeoutSeconds: 600,
    });
    expect(optionsSummary(options, CAPS)).toBe(
      "Runs in /workspace/build · a stop will be requested after 10 min · 2 variables",
    );
    expect(
      validateRunOptions({ ...options, timeoutSeconds: 901 }, CAPS),
    ).toEqual({ timeoutSeconds: "From 1 second to 15 min" });
  });

  it("looks for a working folder in its parent's listing (EE lists a missing path as empty)", () => {
    expect(folderLookup("/workspace/src/app/")).toEqual({
      parent: "src",
      name: "app",
    });
    expect(folderLookup("build")).toEqual({ parent: "", name: "build" });
    expect(folderLookup("")).toBeNull();
    const listing = {
      entries: [
        { name: "app", size: 4096, mode: "755", directory: true },
        { name: "link", size: 7, mode: "777" },
      ],
    };
    expect(folderMissing("src/app", listing)).toBe(false);
    expect(folderMissing("src/nope", listing)).toBe(true);
    // Not marked a directory may be a symlink (EE lists with lstat): EE decides.
    expect(folderMissing("src/link", listing)).toBe(false);
    expect(folderMissing("src/app", null)).toBe(false);
  });

  it("names the output cap in the unit EE states it", () => {
    expect(formatByteCap(1_000_000)).toBe("1 MB");
    expect(formatByteCap(1_048_576)).toBe("1 MiB");
    expect(formatByteCap(undefined)).toBeNull();
  });
});

describe("Future AGI's words for EE's reasons (unit)", () => {
  it("words a template refusal by who refused it, never naming the operator or a script", () => {
    expect(
      templateReason({
        available: false,
        refusedBy: "host",
        reason:
          "This template's image is not on the container host yet. An operator builds it with deploy/sandbox-images/build.mjs.",
      }),
    ).toBe("Not available on this deployment right now.");
    expect(
      templateReason({ available: false, refusedBy: "workspace", reason: "x" }),
    ).toMatch(/workspace admin can turn it on in Settings/);
    expect(
      templateReason({
        available: false,
        refusedBy: "size",
        reason: "Needs at least Medium (1 CPU, 2 GB).",
      }),
    ).toBe("Needs at least Medium (1 CPU, 2 GB).");
    // An older EE without refusedBy: its text without operator sentences.
    expect(
      templateReason({
        available: false,
        reason: "This template's image is not on the container host yet.",
      }),
    ).toBe("Not offered on this deployment.");
    expect(templateReason({ available: true })).toBeNull();
  });

  it("keeps EE's sentences about this workspace and drops those for the operator", () => {
    expect(
      customerSentences(
        "This workspace offers only Large, and it cannot run here. Large: needs 0.5 CPU more. A workspace owner or admin can offer Small in the workspace's sandbox settings.",
      ),
    ).toBe(
      "This workspace offers only Large, and it cannot run here. Large: needs 0.5 CPU more. A workspace owner or admin can offer Small in the workspace's sandbox settings.",
    );
    expect(
      createRefusalText({
        field: "template",
        reason:
          "None of the templates this deployment lists is offered here, and no other image is. An operator can build the template images or offer another image.",
      }),
    ).toBe(
      "None of the templates this deployment lists is offered here, and no other image is.",
    );
    expect(
      createRefusalText({
        field: "size",
        reason: "An operator can offer Small on this deployment.",
      }),
    ).toBe(
      "None of the sizes this workspace offers can run on this deployment.",
    );
    expect(capReason(4, 4)).toBe(
      "4 visible sandboxes are running, at the workspace limit of 4. Hibernate or delete one before trying to start another.",
    );
  });
});
