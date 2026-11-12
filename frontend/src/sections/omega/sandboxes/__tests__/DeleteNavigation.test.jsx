// DEMO-POLISH-3 (round-5c demo check): after Delete, the app sometimes stayed on the deleted sandbox's
// Activity tab instead of going back to the list. Delete refreshed the sandbox first; the refreshed record
// said "destroyed", so the page redirected its tab to Activity, and that redirect raced the list navigation
// (30 ms apart in the demo's own log, the later one won). Delete must go to the list and nowhere else.
import React from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApi, renderSandboxes, sandboxView } from "./helpers";
import { session, workspace } from "./session-mock";

// Every redirect the page asks for, so a redirect that a later navigation overwrites is still seen. In the
// browser the two land 30 ms apart and either can win; here the record says whether the race exists at all.
const redirects = vi.hoisted(() => []);
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal();
  function RecordingNavigate(props) {
    redirects.push(props.to);
    return actual.Navigate(props);
  }
  return { ...actual, Navigate: RecordingNavigate };
});

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: SandboxDetailPage } = await import("../SandboxDetailPage");
const ID = "sbx_0123456789abcdef01234567";
const LIST = "/dashboard/sandboxes";
const at = (tab) => `${LIST}/${ID}${tab ? `/${tab}` : ""}`;

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
});

/** Every path the router shows, in order, so a redirect that is later overwritten is still caught. */
function recordPaths() {
  const seen = [screen.getByTestId("location").textContent];
  const observer = new MutationObserver(() => {
    const now = screen.getByTestId("location").textContent;
    if (seen.at(-1) !== now) seen.push(now);
  });
  observer.observe(screen.getByTestId("location"), {
    childList: true,
    characterData: true,
    subtree: true,
  });
  return { seen, stop: () => observer.disconnect() };
}

describe("Delete on a sandbox", () => {
  it.each(["run", "files", "overview", "snapshots"])(
    "from its %s tab goes to the list and never to the deleted sandbox's Activity",
    async (tab) => {
      let state = "active";
      const api = fakeApi({
        get: vi.fn(async () => sandboxView({ state })),
        destroy: vi.fn(async () => {
          state = "destroyed";
          return sandboxView({ state });
        }),
      });
      renderSandboxes(<SandboxDetailPage />, { api, route: at(tab) });
      fireEvent.click(await screen.findByTestId("sandbox-delete"));
      redirects.length = 0;
      const paths = recordPaths();
      fireEvent.click(await screen.findByTestId("sandbox-delete-confirm"));
      await waitFor(() => expect(api.destroy).toHaveBeenCalledWith(ID));
      const readsBeforeDelete = 1;
      await waitFor(() =>
        expect(screen.getByTestId("location").textContent).toBe(LIST),
      );
      // Let every pending refetch and effect settle, then look at the whole trail.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      paths.stop();
      expect(screen.getByTestId("location").textContent).toBe(LIST);
      expect(redirects).toEqual([]);
      // The cause: Delete re-read the deleted sandbox while its page was still open, and that page then
      // redirected itself. Nothing re-reads it on the way out now.
      expect(api.get).toHaveBeenCalledTimes(readsBeforeDelete);
      // And the list is refreshed, so it no longer shows the sandbox.
      expect(api.list).toHaveBeenCalled();
      expect(paths.seen).not.toContain(at("activity"));
      expect(paths.seen.at(-1)).toBe(LIST);
      expect(
        await screen.findByText(/Deleted data-cleanup/u),
      ).toBeInTheDocument();
    },
  );

  it("a link to a deleted sandbox's Files tab still lands on its Activity", async () => {
    const api = fakeApi({
      get: vi.fn(async () => sandboxView({ state: "destroyed" })),
    });
    redirects.length = 0;
    renderSandboxes(<SandboxDetailPage />, { api, route: at("files") });
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(at("activity")),
    );
    expect(redirects).toContain(at("activity"));
  });
});
