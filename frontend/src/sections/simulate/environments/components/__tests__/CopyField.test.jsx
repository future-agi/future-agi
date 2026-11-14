import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import CopyField from "../CopyField";

const writeText = vi.fn().mockResolvedValue(undefined);

// userEvent.setup() installs its own clipboard stub, so re-assert ours after.
const stubClipboard = () => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
};

beforeEach(() => {
  writeText.mockClear();
  stubClipboard();
});

const originalExecCommand = document.execCommand;

afterEach(() => {
  vi.clearAllMocks();
  // Restore anything the unavailable-clipboard test mutated so it can't leak
  // into another test file.
  stubClipboard();
  document.execCommand = originalExecCommand;
});

describe("CopyField", () => {
  it("renders the value", () => {
    render(<CopyField value="fai env init demo" />);
    expect(screen.getByText("fai env init demo")).toBeInTheDocument();
  });

  it("copies the value to the clipboard on click", async () => {
    const user = userEvent.setup();
    stubClipboard();
    render(<CopyField value="fai env init demo" />);

    await user.click(screen.getByRole("button", { name: /copy/i }));

    expect(writeText).toHaveBeenCalledWith("fai env init demo");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /copied/i })).toBeInTheDocument(),
    );
  });

  it("does not claim 'Copied' when the clipboard is unavailable", async () => {
    const user = userEvent.setup();
    // No async clipboard API, and the execCommand fallback refuses.
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const execCommand = vi.fn(() => false);
    document.execCommand = execCommand;
    render(<CopyField value="fai env init demo" />);

    await user.click(screen.getByRole("button", { name: /copy/i }));

    // The label stays "Copy to clipboard" — nothing actually copied.
    expect(screen.queryByRole("button", { name: /copied/i })).toBeNull();
  });
});
