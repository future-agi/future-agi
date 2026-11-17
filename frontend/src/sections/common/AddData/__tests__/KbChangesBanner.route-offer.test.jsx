// The knowledge-base changes banner asks for its preview only where this
// build offers the route it posts to (the imports route). Where that route
// is not offered nothing is sent and nothing shows; the banner looked the
// same before (a failed preview shows nothing). The saved sources route
// does not decide it: the banner never calls it.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  DOOR_ROUTES,
  datasetSourcesOffered,
  routeOffered,
} from "src/sections/common/Collab/routeOffer";
import { IMPORTS_PATH } from "../mappingModel";
import KbChangesBanner from "../KbChangesBanner";

const h = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn(), absent: null }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, post: h.post, get: h.get },
  };
});
// A case may name the routes this build lacks; otherwise the real list.
vi.mock("src/launch/absentPathGuard", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    absentTemplateOf: (url, ...rest) =>
      h.absent
        ? h.absent.includes(url)
          ? url
          : null
        : actual.absentTemplateOf(url, ...rest),
  };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const STALE = {
  stale: [
    {
      kb_name: "Refund policy",
      rows: [{ row_id: "row-1", new: { answer: "Within 30 days" } }],
    },
  ],
};
const PREVIEW = {
  op: "preview",
  source: { kind: "generate", mode: "kb_refresh" },
  target: { dataset_id: "ds-kb" },
};

let client;
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <KbChangesBanner datasetId="ds-kb" />
    </QueryClientProvider>,
  );
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

beforeEach(() => {
  h.get.mockReset();
  h.post.mockReset().mockResolvedValue({ data: { result: STALE } });
  h.absent = null;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  h.absent = null;
});

describe("the knowledge-base changes banner", () => {
  it("sends no preview where this build has no sources route", async () => {
    setLaunchOverrideForTest({ launch: true });
    expect(datasetSourcesOffered()).toBe(false);

    mount();
    await settle();

    expect(h.post).not.toHaveBeenCalled();
    expect(h.get).not.toHaveBeenCalled();
    expect(screen.queryByTestId("kb-changes")).toBeNull();
  });

  // Guard (green before and after): where the route is offered the preview
  // is asked for and a changed passage shows.
  it("asks for the preview and shows a changed passage where the route is offered", async () => {
    setLaunchOverrideForTest(null);
    expect(datasetSourcesOffered()).toBe(true);

    mount();

    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    expect(h.post).toHaveBeenCalledWith(IMPORTS_PATH, PREVIEW);
    expect(await screen.findByTestId("kb-changes")).toHaveTextContent(
      "Refund policy changed · 1 golden cites a changed passage",
    );
  });
});

describe("the route the banner posts to decides", () => {
  it("sends no preview where the imports route is not offered, even where sources are", async () => {
    setLaunchOverrideForTest({ launch: true });
    h.absent = [IMPORTS_PATH];
    expect(datasetSourcesOffered()).toBe(true);
    expect(routeOffered(IMPORTS_PATH)).toBe(false);

    mount();
    await settle();

    expect(h.post).not.toHaveBeenCalled();
    expect(h.get).not.toHaveBeenCalled();
    expect(screen.queryByTestId("kb-changes")).toBeNull();
  });

  it("asks for the preview where the imports route is offered, even where sources are not", async () => {
    setLaunchOverrideForTest({ launch: true });
    h.absent = [DOOR_ROUTES.datasetSources];
    expect(datasetSourcesOffered()).toBe(false);
    expect(routeOffered(IMPORTS_PATH)).toBe(true);

    mount();

    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    expect(h.post).toHaveBeenCalledWith(IMPORTS_PATH, PREVIEW);
    expect(await screen.findByTestId("kb-changes")).toHaveTextContent(
      "Refund policy changed · 1 golden cites a changed passage",
    );
  });
});
