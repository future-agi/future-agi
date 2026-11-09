// DEMO-POLISH-3 (verify finding on the first fix): the detail page polls its sandbox every 10 s. A poll that
// lands while the DELETE is still in flight reads "destroyed" before Delete's own success handler runs, and the
// page then redirected its tab to Activity on its own, the same race by another road. The page must know it is
// leaving from the moment Delete is confirmed, and a Delete that fails must leave the page as it was.
import React from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApi, renderSandboxes, sandboxView } from "./helpers";
import { session, workspace } from "./session-mock";

// Every redirect the page asks for, so one that a later navigation overwrites is still seen.
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
const location = () => screen.getByTestId("location").textContent;

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
});

/** Every path the router shows, in order. */
function recordPaths() {
  const seen = [location()];
  const observer = new MutationObserver(() => {
    if (seen.at(-1) !== location()) seen.push(location());
  });
  observer.observe(screen.getByTestId("location"), {
    childList: true,
    characterData: true,
    subtree: true,
  });
  return { seen, stop: () => observer.disconnect() };
}

/** The page's own 10 s poll of its sandbox, fired now. */
const pollOwnRecord = (queryClient) =>
  act(async () => {
    await queryClient.refetchQueries({
      predicate: (query) =>
        query.queryKey.length === 4 &&
        query.queryKey[2] === "sandbox" &&
        query.queryKey[3] === ID,
    });
  });

const settle = (ms = 50) =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("Delete on a sandbox, with the page's poll landing while the DELETE is in flight", () => {
  it.each(["files", "run", "overview"])(
    "from its %s tab never redirects to the deleted sandbox's Activity and ends on the list",
    async (tab) => {
      let state = "active";
      const pending = deferred();
      const api = fakeApi({
        get: vi.fn(async () => sandboxView({ state })),
        destroy: vi.fn(() => {
          // EE marks the sandbox destroyed while it tears the container down; a read now says so.
          state = "destroyed";
          return pending.promise;
        }),
      });
      const { queryClient } = renderSandboxes(<SandboxDetailPage />, {
        api,
        route: at(tab),
      });
      fireEvent.click(await screen.findByTestId("sandbox-delete"));
      redirects.length = 0;
      const paths = recordPaths();
      fireEvent.click(await screen.findByTestId("sandbox-delete-confirm"));
      await waitFor(() => expect(api.destroy).toHaveBeenCalledWith(ID));

      await pollOwnRecord(queryClient);
      await settle();
      expect(api.get).toHaveBeenCalledTimes(2);
      // Still in flight: the page stays where it is and asks for no redirect.
      expect(redirects).toEqual([]);
      expect(location()).toBe(at(tab));

      await act(async () => {
        pending.resolve(sandboxView({ state: "destroyed" }));
      });
      await waitFor(() => expect(location()).toBe(LIST));
      await settle();
      paths.stop();
      expect(redirects).toEqual([]);
      expect(paths.seen).not.toContain(at("activity"));
      expect(paths.seen.at(-1)).toBe(LIST);
      expect(
        await screen.findByText(/Deleted data-cleanup/u),
      ).toBeInTheDocument();
    },
  );

  it("a Delete that fails leaves the page on its tab, says why, and Delete can be tried again", async () => {
    const api = fakeApi({
      get: vi.fn(async () => sandboxView()),
      destroy: vi.fn(async () => {
        throw Object.assign(
          new Error("The sandbox host did not answer. Try again."),
          { status: 503 },
        );
      }),
    });
    const { queryClient } = renderSandboxes(<SandboxDetailPage />, {
      api,
      route: at("files"),
    });
    fireEvent.click(await screen.findByTestId("sandbox-delete"));
    redirects.length = 0;
    fireEvent.click(await screen.findByTestId("sandbox-delete-confirm"));
    await waitFor(() => expect(api.destroy).toHaveBeenCalledTimes(1));
    await settle();
    expect(location()).toBe(at("files"));
    expect(redirects).toEqual([]);
    expect(
      await screen.findByText("The sandbox host did not answer. Try again."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("sandbox-delete")).toBeEnabled();
    expect(screen.getByTestId("sandbox-delete-confirm")).toBeEnabled();

    // The page is not "leaving" any more: if the sandbox is gone after all, the next poll shows its Activity.
    api.get.mockImplementation(async () => sandboxView({ state: "destroyed" }));
    await pollOwnRecord(queryClient);
    await waitFor(() => expect(location()).toBe(at("activity")));
    expect(redirects).toContain(at("activity"));
  });
});
