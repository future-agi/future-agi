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
  const dialog = 'import { Dialog } from "@mui/material";';
  const fields = [
    "const id = <T>(x: T) => x;",
    'export const fields = [id("displayName"), id("email"), id("timeZone")];',
    "export type Field = (typeof fields)[number];",
  ].join("\n");
  write("views/Fields.ts", `${dialog}\n${fields}`);
  const preview =
    'export const Preview = () => <Dialog open aria-label="Account preferences" />;';
  write("views/Preview.stories.tsx", `${dialog}\n${preview}`);
  const multiline = (source: string) =>
    `import {\n  Dialog,\n} from "${source}";\nexport const color = "#abc";`;
  write("views/Multiline.ts", multiline("./dialog"));
  const originalAlias = 'import OriginalDialog from "@mui/material/Dialog";';
  write("views/Aliases.ts", originalAlias);
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
  // Only the story was exempt at base, even when renamed without editing it.
  const reformatted = dialog.slice(0, -1);
  git("mv", "src/views/Fields.ts", "src/views/Fields.tsx");
  write("views/Fields.tsx", `${reformatted}\n${fields.replace("<T>", "<T,>")}`);
  git("mv", "src/views/Preview.stories.tsx", "src/views/Preview.tsx");
  // The import source changes, but the specifier and color lines do not.
  write("views/Multiline.ts", multiline("@mui/material"));
  write(
    "views/Aliases.ts",
    `import NewDialog from "@mui/material/Dialog";\n${originalAlias}`,
  );

  // A conversion that reformats an old import and introduces a new one.
  git("mv", "src/views/Page.jsx", "src/views/Page.tsx");
  write(
    "views/Page.tsx",
    `${legacy.slice(0, -1)}\n${dialog}\n${page}\nexport type Open = boolean;`,
  );
  write(
    "views/Other.jsx",
    `import Dialog from "@mui/material/Dialog";\n${legacy}`,
  );
  write(
    "views/MixedType.ts",
    'import { Button, type Dialog } from "@mui/material";',
  );
  write(
    "views/MixedValue.ts",
    'import { Button, Dialog } from "@mui/material";',
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

it("reports new restricted imports but not ones already linted before a rename", () => {
  const { status, stdout, stderr } = run(
    process.execPath,
    "scripts/lint-ds-changed.mjs",
    "--base",
    "HEAD",
  );
  expect(status, stderr).toBe(1);
  expect(stdout).toMatch(/Page\.tsx:.*error.*bare MUI Dialog/);
  expect(stdout).toMatch(/Other\.jsx:.*error.*bare MUI Dialog/);
  expect(stdout).not.toContain("Fields.tsx:");
  expect(stdout).toMatch(/Preview\.tsx:.*error.*bare MUI Dialog/);
  expect(stdout).toMatch(/colors\.ts:.*error.*\[DS\] Hex colour/);
  expect(stdout).toMatch(/Other\.jsx:.*warning.*EmptyLayout/);
  expect(stdout).not.toContain("MixedType.ts:");
  expect(stdout).toMatch(/MixedValue\.ts:.*error.*bare MUI Dialog/);
  expect(stdout).toMatch(/Multiline\.ts:2:.*error.*bare MUI Dialog/);
  expect(stdout).not.toMatch(/Multiline\.ts:.*Hex colour/);
  expect(stdout).toMatch(/Aliases\.ts:1:.*error.*bare MUI Dialog/);
  expect(stdout).not.toContain("Aliases.ts:2:");
  expect(stdout).toContain("7 design-system error(s) and 1 warning(s)");
});

it("includes typed source files in the full design-system lint", () => {
  const { status, stdout, stderr } = run(
    "npm",
    "run",
    "--silent",
    "lint:ds",
    "--",
    "--format=json",
  );
  expect(status, stderr).toBe(1);
  const reports: { filePath: string; messages: { message: string }[] }[] =
    JSON.parse(stdout);
  const messages = (file: string) =>
    reports.find((r) => r.filePath.endsWith(join("src", file)))?.messages;
  const bareDialog = expect.objectContaining({
    severity: 2,
    message: expect.stringContaining("bare MUI Dialog"),
  });
  expect(stdout).not.toContain("Parsing error");
  expect(messages("views/Page.tsx")).toContainEqual(bareDialog);
  expect(messages("views/colors.ts")).toContainEqual(
    expect.objectContaining({ message: expect.stringContaining("Hex colour") }),
  );
  expect(messages("components/custom-dialog/Wrapper.tsx")).toEqual([]);
  expect(messages("views/Preferences.tsx")).toEqual([]);
  expect(messages("views/MixedType.ts")).toEqual([]);
  expect(messages("views/MixedValue.ts")).toEqual([bareDialog]);
});

it("counts TS and TSX opt-outs against the same budget", () => {
  const { status, stdout, stderr } = run(
    process.execPath,
    "scripts/ds-optouts.mjs",
  );
  expect(status, stderr).toBe(0);
  expect(stdout).toContain("2 opt-out(s)");
});
