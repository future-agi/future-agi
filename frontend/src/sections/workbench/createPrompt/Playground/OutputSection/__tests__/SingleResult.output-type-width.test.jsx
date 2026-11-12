import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PromptWorkbenchContext } from "../../../WorkbenchContext";
import { PROMPT_RESULT_TYPE_OPTIONS } from "../../common";
import SingleResult from "../SingleResult";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({ versions: [], refetch: vi.fn() }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../../../VersionHistory/LabelDropdown/LabelSelectPopover", () => ({
  default: () => null,
}));
vi.mock("src/components/ReactMarkdownWithHighlight", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/prompt/NewPrompt/PromptGenerate/Renderers/JsonOutputRenderer",
  () => ({ default: () => null }),
);
vi.mock("src/components/gridIcon/GridIcon", () => ({ default: () => null }));
vi.mock(
  "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext",
  () => ({ useSingleImageViewContext: () => ({ setImageUrl: vi.fn() }) }),
);
vi.mock("src/components/custom-audio/TestAudioPlayer", () => ({
  default: () => null,
}));
vi.mock("../PromptRunSteps", () => ({
  default: () => null,
  finalAnswerOf: () => null,
}));

const workbench = {
  promptName: "walk-capital",
  selectedVersions: [{ version: "V1", isDraft: false, id: "a" }],
  setModelConfigByIndex: vi.fn(),
  saveAndRun: vi.fn(),
};

function renderOutput() {
  render(
    <MemoryRouter>
      <PromptWorkbenchContext.Provider value={workbench}>
        <SingleResult
          result={{ outputFormat: "string", text: "Paris", metadata: {} }}
          responseFormat={null}
        />
      </PromptWorkbenchContext.Provider>
    </MemoryRouter>,
  );
}

const longestLabel = PROMPT_RESULT_TYPE_OPTIONS.map((o) => o.label).reduce(
  (a, b) => (b.length > a.length ? b : a),
);

// The Output section's view select cut its own label ("Markdowr"): the box
// was sized to the picked label's letter count, and wide letters ("M", "w")
// need more than an average letter's room.
describe("Prompt Output view select", () => {
  it("offers Markdown as its longest view", () => {
    expect(longestLabel).toBe("Markdown");
  });

  it("leaves room for the longest view name, whichever view is picked", () => {
    renderOutput();
    const select = screen.getByDisplayValue("Raw");

    // More characters of room than "Markdown" has letters, already on Raw,
    // so the box does not change size (or cut) when Markdown is picked.
    expect(Number(select.getAttribute("size"))).toBeGreaterThan(
      longestLabel.length,
    );
    // The size attribute counts average characters; the minimum width in
    // "0"-widths (ch) is what keeps "Markdown"'s last letter visible.
    const minWidth = select.style.minWidth;
    expect(minWidth).toMatch(/^\d+(\.\d+)?ch$/);
    expect(parseFloat(minWidth)).toBeGreaterThan(longestLabel.length);
  });
});
