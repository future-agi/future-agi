import React from "react";
import { render } from "src/utils/test-utils";
import { describe, expect, it, vi } from "vitest";
import DrawerHeader from "../DrawerHeader";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// Looked up by accessible name (the icons are a design-system detail).
const shareButton = (container) =>
  container.querySelector('[aria-label="Share trace"]');
const newTabButton = (container) =>
  container.querySelector('[aria-label="Open in new tab"]');

describe("trace drawer header actions", () => {
  it("offers share and open-in-new-tab when the host handles them", () => {
    const { container } = render(
      <DrawerHeader
        open
        onClose={vi.fn()}
        onShare={vi.fn()}
        onOpenNewTab={vi.fn()}
      />,
    );

    expect(shareButton(container)).not.toBeNull();
    expect(newTabButton(container)).not.toBeNull();
  });

  it("offers neither when the host hands over no handler", () => {
    const { container } = render(<DrawerHeader open onClose={vi.fn()} />);

    expect(shareButton(container)).toBeNull();
    expect(newTabButton(container)).toBeNull();
  });
});
