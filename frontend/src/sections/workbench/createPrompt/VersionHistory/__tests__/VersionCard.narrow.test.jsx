import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { isZero } from "src/sections/workbench/__tests__/promptsContrast";
import { PromptWorkbenchContext } from "../../WorkbenchContext";
import VersionCard from "../VersionCard";

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/custom-model-tools", () => ({ default: () => null }));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-model-options/CustomModelOptions", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("../LabelDropdown/LabelDropdown", () => ({ default: () => null }));
vi.mock("../LabelDropdown/PromptLabel", () => ({ default: () => null }));

const LONG_NAME = "Customer support reply for delayed international orders";

// The prompt name on a History card never shrank, so on a
// narrow drawer a long name pushed the version name (V2) out of the card.
describe("History card with a long prompt name", () => {
  it("lets the prompt name end in an ellipsis so the version name stays visible", () => {
    render(
      <PromptWorkbenchContext.Provider
        value={{ onRestoreVersion: vi.fn(), selectedVersions: [] }}
      >
        <VersionCard
          version={{
            id: "id-V2",
            template_name: LONG_NAME,
            template_version: "V2",
            is_draft: false,
            created_at: "2026-11-10T12:00:00Z",
            labels: [],
            variable_names: {},
          }}
        />
      </PromptWorkbenchContext.Provider>,
    );

    const name = screen.getByText(LONG_NAME);
    expect(getComputedStyle(name).whiteSpace).toBe("nowrap");
    expect(getComputedStyle(name).textOverflow).toBe("ellipsis");
    expect(isZero(getComputedStyle(name.parentElement).minWidth)).toBe(true);
    expect(getComputedStyle(name.parentElement).flexShrink).not.toBe("0");

    const versionName = screen.getByText("V2");
    expect(getComputedStyle(versionName).flexShrink).toBe("0");
    expect(getComputedStyle(versionName).whiteSpace).toBe("nowrap");
  });
});
