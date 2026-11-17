import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OmegaConsoleLayout from "../OmegaConsoleLayout";
import { useOmegaConsoleChrome } from "../console-chrome";
import { ShellTestProviders, ensureWebStorage } from "./test-providers";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "u1" }, logout: () => {} }),
}));

// Stands in for lane console-frame's page: it reports the frame state the
// way the real page will, through the hook the shell hands it.
function FramePage({ shown }) {
  const { setFrameShown } = useOmegaConsoleChrome();
  useEffect(() => {
    setFrameShown(shown);
  }, [setFrameShown, shown]);
  return <div data-testid="frame-page" />;
}

FramePage.propTypes = { shown: PropTypes.bool.isRequired };

function Harness() {
  const [shown, setShown] = React.useState(false);
  return (
    <ShellTestProviders>
      <MemoryRouter initialEntries={["/dashboard/console"]}>
        <OmegaConsoleLayout>
          <FramePage shown={shown} />
          <button type="button" onClick={() => setShown((v) => !v)}>
            toggle frame
          </button>
        </OmegaConsoleLayout>
      </MemoryRouter>
    </ShellTestProviders>
  );
}

describe("useOmegaConsoleChrome", () => {
  beforeEach(() => ensureWebStorage());

  it("hides the shell's switcher bar while the frame is shown", async () => {
    render(<Harness />);
    const layout = screen.getByTestId("omega-console-layout");
    expect(layout).toHaveAttribute("data-frame-shown", "false");
    expect(screen.getByTestId("omega-console-bar")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "toggle frame" }));
    expect(layout).toHaveAttribute("data-frame-shown", "true");
    expect(screen.queryByTestId("omega-console-bar")).toBeNull();
    // The page itself stays mounted; only the shell chrome goes away.
    expect(screen.getByTestId("frame-page")).toBeInTheDocument();

    // If the frame goes away again (refused, crashed), the way back returns.
    await userEvent.click(screen.getByRole("button", { name: "toggle frame" }));
    expect(screen.getByTestId("omega-console-bar")).toBeInTheDocument();
  });
});
