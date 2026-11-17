// Regression controls for quoted-list whitespace and delimiter fragmentation.
// Synthetic values are minted per case and never included in diagnostics.
import { test } from "node:test";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { auditBuffer } from "./evidence-audit/audit.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const names = ["tokens", "password", "secret", "api_key"];
const separators = {
  tab: "\t",
  newline: "\n",
  return: "\r",
  comma: ",",
  open: "[",
  close: "]",
};
const forms = (name, value) => {
  const json = JSON.stringify({ [name]: value });
  return {
    direct: json,
    escaped: JSON.stringify({ body: json }),
    text: `${name}=${value}\n`,
    base64: JSON.stringify({ body: Buffer.from(json).toString("base64") }),
    nested: JSON.stringify({
      body: JSON.stringify({ postData: Buffer.from(json).toString("base64") }),
    }),
    base64url: JSON.stringify({
      body: Buffer.from(json).toString("base64url"),
    }),
    header: JSON.stringify({ name, value }),
    evaluate: JSON.stringify({ k: name, v: { s: value } }),
    url: `http://127.0.0.1/?${name}=${encodeURIComponent(value)}`,
  };
};
// Each delimiter-separated piece is shorter than the scalar minimum, while
// the complete credential is long enough. Whitespace likewise stays INSIDE it.
const mint = (separator) =>
  `P7${randomBytes(1).toString("hex")}${separator}q${randomBytes(1).toString("hex")}`;

for (const [style, separator] of Object.entries(separators)) {
  test(`r4 quoted-list ${style}: audit refuses complete items in every representation`, () => {
    const failures = [];
    for (const name of names)
      for (const quote of ['"', "'"])
        for (const mixed of [false, true]) {
          const value = mint(separator);
          const list = `[${mixed ? "keywords," : ""}${quote}${value}${quote}]`;
          for (const [form, body] of Object.entries(forms(name, list))) {
            const report = auditBuffer(Buffer.from(body), "resources/r.json");
            if (!report.refused || !report.hits.some((h) => h.name === name))
              failures.push({ name, form, quote, mixed });
            const serialized = JSON.stringify(report);
            for (const encoded of [value, encodeURIComponent(value)])
              assert.equal(
                serialized.includes(encoded),
                false,
                "value-free report",
              );
          }
        }
    assert.deepEqual(failures, [], "complete quoted values must refuse");
  });

  test(`r4 quoted-list ${style}: production landing never stages credential-bearing evidence`, () => {
    const failures = [];
    for (const name of names) {
      const value = mint(separator);
      for (const [form, body] of Object.entries(forms(name, `["${value}"]`))) {
        const root = mkdtempSync(path.join(tmpdir(), "quoted-list-land-"));
        const group = path.join(root, "e2e/walk-results/group");
        mkdirSync(group, { recursive: true });
        symlinkSync(here, path.join(root, "e2e/scripts"));
        writeFileSync(path.join(group, "summary.json"), body);
        const run = spawnSync(
          "bash",
          [
            path.join(here, "../walks/vm/land.sh"),
            "quoted",
            "1",
            "e2e/walk-results/**/summary.json",
            "--",
            "true",
          ],
          { cwd: root, encoding: "utf8", env: { ...process.env, HOME: root } },
        );
        const landed = path.join(
          root,
          "e2e/walk-landed/quoted/e2e/walk-results",
        );
        const status = path.join(landed, "command/command-exit.txt");
        if (
          run.status !== 1 ||
          !existsSync(status) ||
          readFileSync(status, "utf8").trim() === "0" ||
          existsSync(path.join(landed, "group/summary.json"))
        )
          failures.push({ name, form });
      }
    }
    assert.deepEqual(
      failures,
      [],
      "production must refuse each quoted credential",
    );
  });
}

test("r4 quoted-list grammar: unsupported syntax fails closed", () => {
  for (const list of [
    '["identifier"]',
    "['identifier']",
    "[[identifier]]",
    "[identifier,,other]",
    "[identifier other]",
    "[identifier,]",
    '["unterminated]',
  ])
    for (const name of names)
      for (const body of Object.values(forms(name, list)))
        assert.equal(
          auditBuffer(Buffer.from(body), "resources/r.json").refused,
          true,
          "unsupported list syntax",
        );
});

test("r4 quoted-list query boundaries: an unrelated value cannot swallow a credential pair", () => {
  for (const boundary of ["?", "&", "#", ";"]) {
    const value = `Pw7${randomBytes(4).toString("hex")}`;
    for (const separator of ["\n", "\\n"]) {
      const body = `http://127.0.0.1/?note=[keywords,${separator}${boundary}password=${value}]`;
      // Code resources deliberately omit free-text pair detection. The URL
      // detector itself must retain every independently located query pair.
      const report = auditBuffer(Buffer.from(body), "resources/source.mjs");
      assert.equal(report.refused, true, "query boundaries remain independent");
      assert.ok(
        report.hits.some(
          (h) => h.kind === "url-param" && h.name === "password",
        ),
      );
      assert.equal(JSON.stringify(report).includes(value), false);
    }
  }
});

// No actual or serialized line end may rescue these URL boundaries. Cover the
// complete-list reader, ordinary scalars, and a rejected list's scalar fallback.
const contiguousQuery = (form, boundary, name, value) => {
  const notes = {
    empty: "",
    scalar: "keywords",
    bracketed: "[keywords, phrases.lower]",
    "bracket-fallback": "[keywords,",
  };
  return `// http://127.0.0.1/?note=${notes[form]}${boundary}${name}=${value}${form === "bracket-fallback" ? "]" : ""}`;
};

for (const boundary of ["?", "&", "#", ";"])
  for (const form of ["empty", "scalar", "bracketed", "bracket-fallback"]) {
    test(`r5 contiguous query ${boundary} ${form}: audit retains each URL pair`, () => {
      const failures = [];
      for (const name of ["password", "api_tokens", "key"])
        for (const resource of [
          "resources/source.mjs",
          "resources/source.ts",
          "resources/r.json",
        ]) {
          const value = `Pw7${randomBytes(4).toString("hex")}`;
          const body = contiguousQuery(form, boundary, name, value);
          assert.equal(/[\r\n]|\\[nr]/.test(body), false, "no line-end rescue");
          const report = auditBuffer(Buffer.from(body), resource);
          if (
            !report.refused ||
            !report.hits.some((h) => h.kind === "url-param" && h.name === name)
          )
            failures.push({ name, resource });
          assert.equal(JSON.stringify(report).includes(value), false);
        }
      assert.deepEqual(failures, [], "each contiguous URL pair must refuse");
    });

    test(`r5 contiguous query ${boundary} ${form}: production landing refuses code resources`, () => {
      const value = `Pw7${randomBytes(4).toString("hex")}`;
      const body = contiguousQuery(form, boundary, "api_tokens", value);
      assert.equal(/[\r\n]|\\[nr]/.test(body), false, "no line-end rescue");
      const root = mkdtempSync(path.join(tmpdir(), "contiguous-query-land-"));
      const group = path.join(root, "e2e/walk-results/group");
      mkdirSync(path.join(group, "resources"), { recursive: true });
      symlinkSync(here, path.join(root, "e2e/scripts"));
      writeFileSync(path.join(group, "resources/source.mjs"), body);
      writeFileSync(path.join(group, "steps.txt"), "walk rc=0\n");
      const run = spawnSync(
        "bash",
        [
          path.join(here, "../walks/vm/land.sh"),
          "contiguous",
          "1",
          "e2e/walk-results/**/*",
          "--",
          "true",
        ],
        { cwd: root, encoding: "utf8", env: { ...process.env, HOME: root } },
      );
      const landed = path.join(
        root,
        "e2e/walk-landed/contiguous/e2e/walk-results",
      );
      assert.equal(
        run.status,
        1,
        "credential-bearing code must refuse landing",
      );
      assert.equal(
        readFileSync(
          path.join(landed, "command/command-exit.txt"),
          "utf8",
        ).trim(),
        "86",
      );
      assert.equal(
        existsSync(path.join(landed, "group/resources/source.mjs")),
        false,
      );
      assert.equal(
        readFileSync(path.join(landed, "group/steps.txt"), "utf8"),
        "walk rc=0\n",
      );
      const serialized = readFileSync(
        path.join(landed, "command/evidence-audit.json"),
        "utf8",
      );
      const report = JSON.parse(serialized);
      assert.equal(report.refused, true);
      assert.ok(
        report.hits.some(
          (h) => h.kind === "url-param" && h.name === "api_tokens",
        ),
      );
      assert.deepEqual(report.quarantined, [
        "e2e/walk-results/group/resources/source.mjs",
      ]);
      assert.deepEqual(report.quarantineFailed, []);
      assert.equal(report.stage.complete, true);
      assert.equal(existsSync(path.join(group, "resources/source.mjs")), false);
      assert.equal(
        `${serialized}${run.stdout}${run.stderr}`.includes(value),
        false,
      );
    });
  }

test("r5 contiguous query: bare identifier lists and redacted values stay clean", () => {
  for (const boundary of ["?", "&", "#", ";"])
    for (const form of ["empty", "scalar", "bracketed"])
      for (const [name, value] of [
        ["tokens", "[keywords, phrases.lower]"],
        ["api_tokens", "[REDACTED]"],
        ["password", "[REDACTED]"],
      ]) {
        const body = contiguousQuery(form, boundary, name, value);
        const report = auditBuffer(Buffer.from(body), "resources/source.mjs");
        assert.equal(report.refused, false, "safe URL values remain clean");
      }
});

test("r4 bracketed text: unrelated values cannot swallow nested credential pairs", () => {
  const failures = [];
  for (const separator of ["\n", "\r", '"', "'"]) {
    const value = `Pw7${randomBytes(4).toString("hex")}`;
    const raw = `note=[keywords,${separator}password=${value}${separator}]`;
    const bodies = [
      raw,
      JSON.stringify({ body: raw }),
      JSON.stringify({ body: JSON.stringify({ body: raw }) }),
      JSON.stringify({ body: Buffer.from(raw).toString("base64") }),
    ];
    for (const [form, body] of bodies.entries()) {
      const report = auditBuffer(Buffer.from(body), "resources/r.json");
      if (!report.refused || !report.hits.some((h) => h.name === "password"))
        failures.push({ separator: separator.charCodeAt(0), form });
      assert.equal(JSON.stringify(report).includes(value), false);
    }
  }
  assert.deepEqual(failures, [], "nested pairs remain independently visible");
});

test("r4 quoted-list grammar: actual and serialized inter-item whitespace stays clean", () => {
  for (const ws of [
    " ",
    "\t",
    "\n",
    "\r",
    "\\t",
    "\\n",
    "\\r",
    "\\u0009",
    "\\u000a",
    "\\u000d",
    "\\u0020",
  ])
    for (const list of [
      `[${ws}identifier${ws}]`,
      `[keywords,${ws}phrases.lower]`,
      "[]",
    ])
      for (const body of Object.values(forms("tokens", list)))
        assert.equal(
          auditBuffer(Buffer.from(body), "resources/r.json").refused,
          false,
          "identifier separators are not credential contents",
        );
});
