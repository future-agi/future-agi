import { describe, expect, it } from "vitest";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const src = path.resolve(process.cwd(), "src");
function sources(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (["__tests__", "generated", "contracts"].includes(entry.name)) return [];
    const file = path.join(dir, entry.name);
    return entry.isDirectory()
      ? sources(file)
      : /\.[jt]sx?$/.test(file) && !/\.(test|spec)\./.test(file)
        ? [file]
        : [];
  });
}
describe("personal-key route contract", () => {
  it("removes the fetch-or-create alias and dormant bootstrap component", () => {
    const axios = readFileSync(path.join(src, "utils/axios.js"), "utf8");
    expect(axios).not.toContain('apiPath("/accounts/keys/")');
    expect(
      existsSync(path.join(src, "sections/keys/api-key-container.jsx")),
    ).toBe(false);
    expect(axios).toContain(
      'generateSecretKey: apiPath("/accounts/key/generate_secret_key/")',
    );
  });
  it("has no route or component calling the retired bootstrap", () => {
    const callers = sources(src).filter((file) =>
      /endpoints\.keys\.keys\b|(?:axios|fetch)\s*\.?\s*(?:get\s*)?\(\s*["'`]\/accounts\/keys\//.test(
        readFileSync(file, "utf8"),
      ),
    );
    expect(callers.map((file) => path.relative(src, file))).toEqual([]);
  });
});
