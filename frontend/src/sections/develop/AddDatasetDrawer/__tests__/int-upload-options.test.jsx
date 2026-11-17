// DSX launch integration (L2 verify 2 should-fix 0, the capability contract): where the
// upload route ignores import_options (dev), the New dataset upload offers no
// Flatten / Read JSON switches; the prototype keeps them.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ImportFileExtras from "../ImportFileExtras";
import { uploadAccepts } from "../uploadBody";

const here = dirname(fileURLToPath(import.meta.url));
const props = {
  text: "a,b\n1,2\n",
  fileName: "f.csv",
  parsed: { rejected: [] },
  flatten: false,
  parseJson: false,
  onFlatten: vi.fn(),
  onParseJson: vi.fn(),
};

describe("upload import options", () => {
  it("shows both switches where the route keeps them", () => {
    render(<ImportFileExtras {...props} />);
    expect(screen.getByLabelText("Flatten nested objects")).toBeTruthy();
    expect(screen.getByLabelText("Read JSON inside text cells")).toBeTruthy();
  });

  it("offers neither where the route ignores import_options", () => {
    render(<ImportFileExtras {...props} optionsShown={false} />);
    expect(screen.queryByLabelText("Flatten nested objects")).toBe(null);
    expect(screen.queryByLabelText("Read JSON inside text cells")).toBe(null);
    expect(screen.getByTestId("import-raw-toggle")).toBeTruthy();
  });

  it("dev's route ignores import_options; the prototype keeps them", () => {
    expect(uploadAccepts("import_options", false)).toBe(false);
    expect(uploadAccepts("import_options", true)).toBe(true);
    expect(
      readFileSync(join(here, "..", "UploadFileModal.jsx"), "utf8"),
    ).toMatch(/optionsShown=\{uploadAccepts\("import_options"\)\}/);
  });
});
