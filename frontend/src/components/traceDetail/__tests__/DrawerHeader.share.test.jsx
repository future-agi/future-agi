import React from "react";
import { render } from "src/utils/test-utils";
import { describe, expect, it, vi } from "vitest";
import DrawerHeader from "../DrawerHeader";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const shareButton = (container) =>
  container.querySelector('[data-icon="basil:share-outline"]');
const newTabButton = (container) =>
  container.querySelector('[data-icon="iconoir:open-new-window"]');

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
