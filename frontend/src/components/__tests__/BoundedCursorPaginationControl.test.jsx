import { act, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import BoundedCursorPaginationControl from "../BoundedCursorPaginationControl";

const installObserver = () => {
  let callback;
  class Observer {
    constructor(nextCallback) {
      callback = nextCallback;
    }
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal("IntersectionObserver", Observer);
  return (isIntersecting) => act(() => callback([{ isIntersecting }]));
};

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

describe("BoundedCursorPaginationControl", () => {
  it("loads each new continuation once while the sentinel remains visible", async () => {
    const intersect = installObserver();
    const firstPage = deferred();
    const secondPage = deferred();
    const loadNextPage = vi
      .fn()
      .mockImplementationOnce(() => firstPage.promise)
      .mockImplementationOnce(() => secondPage.promise);
    const { rerender } = render(
      <BoundedCursorPaginationControl
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "cursor-1",
            loadNextPage,
          },
        ]}
        testId="sentinel"
      />,
    );

    intersect(true);
    intersect(true);
    expect(loadNextPage).toHaveBeenCalledTimes(1);

    await act(async () => {
      firstPage.resolve();
      await firstPage.promise;
    });
    rerender(
      <BoundedCursorPaginationControl
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "cursor-2",
            loadNextPage,
          },
        ]}
        testId="sentinel"
      />,
    );
    expect(loadNextPage).toHaveBeenCalledTimes(2);

    await act(async () => {
      secondPage.resolve();
      await secondPage.promise;
    });

    intersect(true);
    rerender(
      <BoundedCursorPaginationControl
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "cursor-2",
            loadNextPage,
          },
        ]}
        testId="sentinel"
      />,
    );
    expect(loadNextPage).toHaveBeenCalledTimes(2);
  });

  it("requires a new viewport entry before advancing a searched continuation", async () => {
    const intersect = installObserver();
    const firstPage = deferred();
    const secondPage = deferred();
    const loadNextPage = vi
      .fn()
      .mockImplementationOnce(() => firstPage.promise)
      .mockImplementationOnce(() => secondPage.promise);
    const { rerender } = render(
      <BoundedCursorPaginationControl
        autoAdvanceWhileVisible={false}
        channels={[
          {
            channelKey: "attributes",
            hasNextPage: true,
            continuationKey: "cursor-1",
            loadNextPage,
          },
        ]}
      />,
    );

    intersect(true);
    expect(loadNextPage).toHaveBeenCalledOnce();
    await act(async () => {
      firstPage.resolve();
      await firstPage.promise;
    });
    rerender(
      <BoundedCursorPaginationControl
        autoAdvanceWhileVisible={false}
        channels={[
          {
            channelKey: "attributes",
            hasNextPage: true,
            continuationKey: "cursor-2",
            loadNextPage,
          },
        ]}
      />,
    );

    await act(async () => undefined);
    expect(loadNextPage).toHaveBeenCalledOnce();

    intersect(false);
    intersect(true);
    expect(loadNextPage).toHaveBeenCalledTimes(2);
    await act(async () => {
      secondPage.resolve();
      await secondPage.promise;
    });
  });

  it("advances independent catalog and attribute channels together", async () => {
    const intersect = installObserver();
    const loadCatalog = vi.fn().mockResolvedValue(undefined);
    const loadAttributes = vi.fn().mockResolvedValue(undefined);
    render(
      <BoundedCursorPaginationControl
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "catalog-2",
            loadNextPage: loadCatalog,
          },
          {
            channelKey: "attributes",
            hasNextPage: true,
            continuationKey: "attribute-2",
            loadNextPage: loadAttributes,
          },
        ]}
      />,
    );

    intersect(true);
    await act(async () => undefined);
    expect(loadCatalog).toHaveBeenCalledOnce();
    expect(loadAttributes).toHaveBeenCalledOnce();
  });

  it("allows the same continuation after its logical chain resets", async () => {
    const intersect = installObserver();
    const loadFirstProject = vi.fn().mockResolvedValue(undefined);
    const loadSecondProject = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <BoundedCursorPaginationControl
        resetKey="project-one"
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "cursor-2",
            loadNextPage: loadFirstProject,
          },
        ]}
      />,
    );

    intersect(true);
    await waitFor(() => expect(loadFirstProject).toHaveBeenCalledOnce());

    rerender(
      <BoundedCursorPaginationControl
        resetKey="project-two"
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "cursor-2",
            loadNextPage: loadSecondProject,
          },
        ]}
      />,
    );

    await waitFor(() => expect(loadSecondProject).toHaveBeenCalledOnce());
  });

  it("requires an explicit retry, retries only failures, and does not auto-repeat the retried continuation", async () => {
    const intersect = installObserver();
    const loadCatalog = vi.fn().mockResolvedValue(undefined);
    const loadAttributes = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <BoundedCursorPaginationControl
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "catalog-2",
            loadNextPage: loadCatalog,
          },
          {
            channelKey: "attributes",
            continuationKey: "attribute-2",
            error: true,
            loadNextPage: loadAttributes,
          },
        ]}
        retryLabel="Retry page"
      />,
    );

    intersect(true);
    expect(loadCatalog).not.toHaveBeenCalled();
    await act(async () => screen.getByRole("button").click());
    expect(loadCatalog).not.toHaveBeenCalled();
    expect(loadAttributes).toHaveBeenCalledOnce();

    rerender(
      <BoundedCursorPaginationControl
        channels={[
          {
            channelKey: "attributes",
            hasNextPage: true,
            continuationKey: "attribute-2",
            loadNextPage: loadAttributes,
          },
        ]}
        retryLabel="Retry page"
      />,
    );
    await act(async () => undefined);
    intersect(true);
    expect(loadAttributes).toHaveBeenCalledOnce();
  });

  it("uses the supplied scroll root", () => {
    let observedRoot;
    class Observer {
      constructor(_callback, options) {
        observedRoot = options.root;
      }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("IntersectionObserver", Observer);
    const root = document.createElement("div");
    const rootRef = createRef();
    rootRef.current = root;

    render(
      <BoundedCursorPaginationControl
        channels={[
          {
            channelKey: "catalog",
            hasNextPage: true,
            continuationKey: "cursor-2",
            loadNextPage: vi.fn(),
          },
        ]}
        rootRef={rootRef}
      />,
    );
    expect(observedRoot).toBe(root);
  });
});
