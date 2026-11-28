import { execFileSync, spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterAll, beforeAll, expect, it } from "vitest";

let root: string;
const write = (file: string, text: string) => {
  const path = join(root, "src", file);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${text}\n`);
};
const git = (...args: string[]) => execFileSync("git", args, { cwd: root });
const run = (cmd: string, ...args: string[]) =>
  spawnSync(cmd, args, { cwd: root, encoding: "utf8" });

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), "ds-lint-"));
  mkdirSync(join(root, "scripts"));
  for (const file of [
    "package.json",
    ".eslintrc.ds.cjs",
    "scripts/lint-ds-changed.mjs",
    "scripts/ds-optouts.mjs",
  ]) {
    copyFileSync(join(process.cwd(), file), join(root, file));
  }
  symlinkSync(join(process.cwd(), "node_modules"), join(root, "node_modules"));

  const page = [
    'const title = "Preferences for this account";',
    "export default function SettingsPage() {",
    '  return <main aria-label="Settings">{title}</main>;',
    "}",
  ].join("\n");
  const legacy = 'import EmptyLayout from "src/components/EmptyLayout";';
  write("views/Page.jsx", `${legacy}\n${page}`);
  git("init", "--quiet");
  git("add", "src");
  git(
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.org",
    "commit",
    "--no-gpg-sign",
    "-qm",
    "base",
  );
  git("mv", "src/views/Page.jsx", "src/views/Page.tsx");

  const dialog = 'import { Dialog } from "@mui/material";';
  // A conversion that reformats an old import and introduces a new one.
  write(
    "views/Page.tsx",
    `${legacy.slice(0, -1)}\n${dialog}\n${page}\nexport type Open = boolean;`,
  );
  write(
    "views/Other.jsx",
    `import Dialog from "@mui/material/Dialog";\n${legacy}`,
  );
  write(
    "views/Preferences.tsx",
    [
      'import type { Dialog as BarrelDialog } from "@mui/material";',
      'import type { default as Dialog, DialogProps } from "@mui/material/Dialog";',
      'import { dialogClasses } from "@mui/material/Dialog";',
      'import { ClosableDialog } from "src/components/custom-dialog";',
      "export type DialogComponent = typeof Dialog | typeof BarrelDialog;",
      "export const Preferences = (props: DialogProps) =>",
      "  <ClosableDialog {...props} className={dialogClasses.root} />;",
    ].join("\n"),
  );
  const optout = [
    "// eslint-disable-next-line",
    "no-restricted-syntax -- ds: brand",
  ].join(" ");
  const brand = `${optout}\nexport const brand: string = "#abc";`;
  write("views/colors.ts", `${brand}\nexport const invalid: string = "#def";`);
  write("components/custom-dialog/Wrapper.tsx", `${dialog}\n${brand}`);
  git("add", "src");
});

afterAll(() => rmSync(root, { recursive: true, force: true }));

it("rejects a new Dialog import in JSX and converted TSX", () => {
  const { status, stdout, stderr } = run(
    process.execPath,
    "scripts/lint-ds-changed.mjs",
    "--base",
    "HEAD",
  );
  expect(status, stderr).toBe(1);
  expect(stdout).toMatch(/Page\.tsx:.*error.*bare MUI Dialog/);
  expect(stdout).toMatch(/Other\.jsx:.*error.*bare MUI Dialog/);
  expect(stdout).toMatch(/colors\.ts:.*error.*\[DS\] Hex colour/);
  expect(stdout).toMatch(/Other\.jsx:.*warning.*EmptyLayout/);
  expect(stdout).toContain("3 design-system error(s) and 1 warning(s)");
});

it("includes typed source files in the full design-system lint", () => {
  const { status, stdout, stderr } = run("npm", "run", "--silent", "lint:ds");
  expect(status, stderr).toBe(1);
  expect(stdout).toContain("Page.tsx");
  expect(stdout).toContain("colors.ts");
  expect(stdout).not.toContain("Parsing error");
  expect(stdout).not.toContain("Wrapper.tsx");
  expect(stdout).not.toContain("Preferences.tsx");
});

it("counts TS and TSX opt-outs against the same budget", () => {
  const { status, stdout, stderr } = run(
    process.execPath,
    "scripts/ds-optouts.mjs",
  );
  expect(status, stderr).toBe(0);
  expect(stdout).toContain("2 opt-out(s)");
});
