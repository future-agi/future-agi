import { describe, expect, it } from "vitest";

import { secretNameProblem } from "../lib/secretForm";
import {
  referenceableSecrets,
  secretRefsProblem,
  settingsProblems,
} from "../lib/settingsForm";
import {
  VAULT_RESERVED_NAMES,
  VAULT_RESERVED_PREFIXES,
  injectionNameProblem,
  isExpired,
  reservedNameReason,
} from "../lib/vaultNames";

// EE credential-vault.ts isReservedEnvironmentInjectionName at 8744f580, copied
// by hand: if EE's set changes, this pin and vaultNames.js change together.
const EE_RESERVED = [
  "PATH",
  "HOME",
  "SHELL",
  "PWD",
  "OLDPWD",
  "SHLVL",
  "LANG",
  "TERM",
  "TMP",
  "TEMP",
  "TMPDIR",
  "SYSTEMROOT",
  "WINDIR",
  "NODE_OPTIONS",
  "NODE_PATH",
  "BASH_ENV",
  "ENV",
  "CDPATH",
  "IFS",
  "PYTHONPATH",
  "PYTHONHOME",
  "RUBYOPT",
  "PERL5OPT",
];

const NOW = Date.parse("2026-10-19T12:00:00.000Z");
const active = (name, extra = {}) => ({ name, status: "active", ...extra });

describe("names the vault injects into a deployment (EE resolveEnvironmentCredentials)", () => {
  it("mirrors EE's reserved set and prefixes exactly", () => {
    expect([...VAULT_RESERVED_NAMES].sort()).toEqual([...EE_RESERVED].sort());
    expect(VAULT_RESERVED_PREFIXES.map((entry) => entry.prefix)).toEqual([
      "OMEGA_",
      "LD_",
      "DYLD_",
    ]);
  });

  it("refuses at Add every name EE would refuse at container start", () => {
    for (const name of EE_RESERVED) {
      expect(secretNameProblem(name), name).toMatch(
        /is a system variable a deployment cannot receive\. Pick another name\./,
      );
    }
    // The names the verifier's repro showed slipping through.
    for (const name of [
      "OMEGA_API_KEY",
      "PATH",
      "HOME",
      "NODE_OPTIONS",
      "LD_PRELOAD",
      "PYTHONPATH",
      "DYLD_X",
    ]) {
      expect(secretNameProblem(name), name).not.toBeNull();
    }
    expect(secretNameProblem("OMEGA_API_KEY")).toBe(
      "Names starting OMEGA_ are kept for Omega; a deployment cannot receive them. Pick another name.",
    );
    expect(secretNameProblem("LD_PRELOAD")).toMatch(
      /starting LD_ are kept for the system loader/,
    );
    expect(secretNameProblem("DYLD_X")).toMatch(
      /starting DYLD_ are kept for the system loader/,
    );
    // The deployment's own names keep their plainer message.
    expect(secretNameProblem("OMEGA_SERVE_TOKEN")).toMatch(
      /set by the deployment itself/,
    );
  });

  it("anchors prefixes at the start and matches the set exactly", () => {
    for (const name of [
      "OPENAI_API_KEY",
      "FA_OMEGA_KEY",
      "MY_LD_PATH",
      "XDYLD_OK",
      "PATHS",
      "HOME_DIR",
      "OMEGA",
      "_PRIVATE",
    ]) {
      expect(secretNameProblem(name), name).toBeNull();
      expect(reservedNameReason(name), name).toBeNull();
    }
  });

  it("reads an expiry the way EE does: past or now is expired, missing or unparseable is not", () => {
    expect(isExpired({ expiresAt: "2026-10-19T11:59:59.000Z" }, NOW)).toBe(
      true,
    );
    expect(isExpired({ expiresAt: "2026-10-19T12:00:00.000Z" }, NOW)).toBe(
      true,
    );
    expect(isExpired({ expiresAt: "2026-10-19T12:00:01.000Z" }, NOW)).toBe(
      false,
    );
    expect(isExpired({}, NOW)).toBe(false);
    expect(isExpired({ expiresAt: "soon" }, NOW)).toBe(false);
    expect(isExpired(undefined, NOW)).toBe(false);
  });
});

describe("the wizard's picker offers only what EE will inject", () => {
  it("lists reserved, expired, ambiguous and lower-case secrets as unusable, with the reason", () => {
    const secrets = referenceableSecrets(
      [
        active("OPENAI_API_KEY"),
        active("OMEGA_API_KEY"),
        active("PATH"),
        active("LD_PRELOAD"),
        active("OLD_KEY", { expiresAt: "2026-10-18T00:00:00.000Z" }),
        active("NEW_KEY", { expiresAt: "2026-12-01T00:00:00.000Z" }),
        active("DUP"),
        active("DUP"),
        active("lower_key"),
        { name: "GONE", status: "revoked" },
      ],
      NOW,
    );
    const byName = Object.fromEntries(secrets.map((s) => [s.name, s]));
    expect(Object.keys(byName).sort()).toEqual([
      "DUP",
      "LD_PRELOAD",
      "NEW_KEY",
      "OLD_KEY",
      "OMEGA_API_KEY",
      "OPENAI_API_KEY",
      "PATH",
      "lower_key",
    ]);
    expect(byName.OPENAI_API_KEY).toEqual({
      name: "OPENAI_API_KEY",
      usable: true,
      reason: null,
    });
    expect(byName.NEW_KEY.usable).toBe(true);
    expect(byName.OMEGA_API_KEY).toEqual({
      name: "OMEGA_API_KEY",
      usable: false,
      reason:
        "Names starting OMEGA_ are kept for Omega; a deployment cannot receive them.",
    });
    expect(byName.PATH).toEqual({
      name: "PATH",
      usable: false,
      reason: "PATH is a system variable a deployment cannot receive.",
    });
    expect(byName.LD_PRELOAD.usable).toBe(false);
    expect(byName.OLD_KEY).toEqual({
      name: "OLD_KEY",
      usable: false,
      reason: "Expired: a deployment cannot receive it.",
    });
    expect(byName.DUP.reason).toBe(
      "More than one active credential has this name.",
    );
    expect(byName.lower_key.reason).toMatch(/Only upper-case names/);
  });
});

describe("the wizard holds a deploy whose picked names the vault will not inject", () => {
  it("names each blocked secret and keeps Continue off until they are unticked", () => {
    const base = { name: "greeter", rows: [] };
    expect(
      settingsProblems({ ...base, secretRefs: ["OPENAI_API_KEY"] }),
    ).toMatchObject({ ok: true, secretError: null });
    expect(
      settingsProblems({ ...base, secretRefs: ["OMEGA_API_KEY"] }),
    ).toMatchObject({
      ok: false,
      secretError:
        "Untick OMEGA_API_KEY to continue: a deployment cannot receive it.",
    });
    expect(secretRefsProblem(["A_KEY", "PATH", "lower", "LD_PRELOAD"])).toBe(
      "Untick PATH, lower, LD_PRELOAD to continue: a deployment cannot receive them.",
    );
    expect(secretRefsProblem([])).toBeNull();
    const many = Array.from({ length: 65 }, (_, i) => `K_${i}`);
    expect(secretRefsProblem(many)).toBe("At most 64 secrets.");
  });

  it("judges a name the same way everywhere", () => {
    expect(injectionNameProblem("OPENAI_API_KEY")).toBeNull();
    expect(injectionNameProblem("lower")).toMatch(/Only upper-case names/);
    expect(injectionNameProblem("NODE_OPTIONS")).toBe(
      "NODE_OPTIONS is a system variable a deployment cannot receive.",
    );
  });
});
