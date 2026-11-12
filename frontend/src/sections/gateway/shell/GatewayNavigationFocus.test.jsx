import React, { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import GatewayNavigationFocus from "./GatewayNavigationFocus";
import GatewayPageFrame from "../components/frame/GatewayPageFrame";

let finishLoading;
function Shell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [palette, setPalette] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [loaded, setLoaded] = useState(false);
  finishLoading = () => setLoaded(true);
  const title = pathname.endsWith("logs") ? "Request logs" : "Models";
  return (
    <GatewayNavigationFocus pathname={pathname}>
      <nav aria-label="AI Gateway">
        <Link to="/dashboard/gateway/models">Models</Link>
        <Link to="/dashboard/gateway/logs">Logs</Link>
        <Link to="/dashboard/gateway/delayed">Delayed page</Link>
      </nav>
      <button type="button" onClick={() => setPalette(true)}>Find a gateway page</button>
      {palette && (
        <div role="dialog" aria-label="Command palette" data-dsx-command-palette="">
          <input aria-label="Command" onKeyDown={(event) => {
            if (event.key === "Enter") {
              setPalette(false);
              navigate("/dashboard/gateway/logs");
            }
          }} />
        </div>
      )}
      <main id="main-content" tabIndex={-1}>
        {pathname.endsWith("delayed") && !loaded ? (
          <button type="button" onClick={() => setLoaded(true)}>Finish loading</button>
        ) : (
          <GatewayPageFrame key={pathname} title={title}>
            <input aria-label="Search" />
            <Link to={`${pathname}?search=changed`}>Change query</Link>
            <Link to="/dashboard/gateway/models/details">Model tab</Link>
            <button type="button" onClick={() => setRefresh(refresh + 1)}>Refresh {refresh}</button>
          </GatewayPageFrame>
        )}
      </main>
    </GatewayNavigationFocus>
  );
}
function mount() {
  return render(<HelmetProvider><MemoryRouter initialEntries={["/dashboard/gateway/models"]}><Shell /></MemoryRouter></HelmetProvider>);
}
afterEach(cleanup);

describe("R3-SHELL-DX-07 gateway destination focus", () => {
  it("leaves a deep-link load alone, then focuses and announces a keyboard rail destination once", async () => {
    mount();
    expect(screen.getByRole("heading", { name: "Models" })).not.toHaveFocus();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    screen.getByRole("link", { name: "Logs" }).focus();
    await userEvent.keyboard("{Enter}");
    const heading = await screen.findByRole("heading", { name: "Request logs" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByRole("status")).toHaveTextContent("Request logs");
    const search = screen.getByRole("textbox", { name: "Search" });
    await userEvent.click(search);
    await userEvent.type(search, "new query");
    expect(search).toHaveFocus();
  });

  it("focuses the destination after keyboard palette selection", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Find a gateway page" }));
    screen.getByRole("textbox", { name: "Command" }).focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("heading", { name: "Request logs" })).toHaveFocus());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not steal focus for pointer navigation, query edits, refreshes or model tabs", async () => {
    mount();
    await userEvent.click(screen.getByRole("link", { name: "Logs" }));
    expect(screen.getByRole("heading", { name: "Request logs" })).not.toHaveFocus();
    const query = screen.getByRole("link", { name: "Change query" });
    query.focus();
    await userEvent.keyboard("{Enter}");
    expect(query).toHaveFocus();
    const refresh = screen.getByRole("button", { name: "Refresh 0" });
    refresh.focus();
    await userEvent.keyboard("{Enter}");
    expect(refresh).toHaveFocus();
    screen.getByRole("link", { name: "Model tab" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("heading", { name: "Models" })).not.toHaveFocus();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("waits for the new page heading rather than focusing loading content", async () => {
    mount();
    screen.getByRole("link", { name: "Delayed page" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("button", { name: "Finish loading" })).not.toHaveFocus();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    act(() => finishLoading());
    await waitFor(() => expect(screen.getByRole("heading", { name: "Models" })).toHaveFocus());
    expect(screen.getByRole("status")).toHaveTextContent("Models");
  });

  it("abandons delayed destination focus once the user interacts again", async () => {
    mount();
    screen.getByRole("link", { name: "Delayed page" }).focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Finish loading" }));
    expect(screen.getByRole("heading", { name: "Models" })).not.toHaveFocus();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });
});
