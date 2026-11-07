import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import PropTypes from "prop-types";

import useUnsavedNavigation from "./useUnsavedNavigation";

function Harness({ dirty }) {
  const guard = useUnsavedNavigation(dirty);
  const location = useLocation();
  const navigate = useNavigate();
  return <>
    <div>{location.pathname}</div>
    <button onClick={() => navigate("/next")}>Go next</button>
    {guard.blocked && <div role="dialog"><button onClick={guard.cancel}>Stay</button><button onClick={guard.proceed}>Leave</button></div>}
  </>;
}
Harness.propTypes = { dirty: PropTypes.bool };
afterEach(cleanup);

describe("editor navigation guard", () => {
  it("blocks router navigation, lets the user stay, and removes interception on unmount", () => {
    window.history.replaceState({ idx: 0 }, "", "/editor");
    const { unmount } = render(<BrowserRouter><Harness dirty /></BrowserRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Go next" }));
    expect(window.location.pathname).toBe("/editor");
    fireEvent.click(screen.getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    unmount();
    render(<BrowserRouter><Harness dirty={false} /></BrowserRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Go next" }));
    expect(window.location.pathname).toBe("/next");
  });

  it("restores native Back before confirmation and resumes it only after Leave", async () => {
    window.history.replaceState({ idx: 0 }, "", "/previous");
    window.history.pushState({ idx: 1 }, "", "/editor");
    render(<BrowserRouter><Harness dirty /></BrowserRouter>);
    await act(async () => window.history.back());
    await screen.findByRole("dialog");
    expect(window.location.pathname).toBe("/editor");
    expect(screen.getByText("/editor")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(window.location.pathname).toBe("/previous"));
    expect(screen.getByText("/previous")).toBeInTheDocument();
  });
});
