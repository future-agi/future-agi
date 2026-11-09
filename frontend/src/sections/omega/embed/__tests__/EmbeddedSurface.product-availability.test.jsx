import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCapabilities } from "src/hooks/useCapabilities";

import EmbeddedSurface from "../EmbeddedSurface";

// Keep useOmegaProducts and productStatus real: only the cached capabilities
// observation is controlled, including unreadable answers with stale data.
const observed = vi.hoisted(() => ({ response: null }));
vi.mock("src/hooks/useCapabilities", () => ({
  useCapabilities: vi.fn(() => observed.response),
}));

const CHILD = "http://127.0.0.1:4191";
const OTHER = "http://127.0.0.1:4192";
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const DOC_B = "m4gLqkrE04O3BMKxGKKPFg";
const hello = (doc = DOC_A) => ({
  type: "embed.hello",
  protocol: "omega-embed/1",
  doc,
});
const denied = () => ({ allowed: false, reason_code: "PRODUCT_NOT_ADMITTED" });
const query = ({
  mode = "cloud",
  product = denied(),
  part = denied(),
  ...state
} = {}) => ({
  data: {
    display_mode: mode,
    features: { omega_sandboxes: product, omega_part_hosted_sandboxes: part },
  },
  isLoading: false,
  isError: false,
  ...state,
});
const admitted = () => query({ product: { allowed: true } });
const paused = () =>
  query({ product: { allowed: false, reason_code: "PRODUCT_PAUSED" } });
const availabilityPost = (sandboxes, targetOrigin = CHILD) => ({
  message: {
    type: "product.availability",
    version: 1,
    products: { sandboxes },
  },
  targetOrigin,
});
const availability = (posted) =>
  posted.filter((entry) => entry.message.type === "product.availability");
const theme = createTheme();

const surfaceTree = (client, props = {}) => {
  const surface = (
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={["/dashboard/console"]}>
        <EmbeddedSurface
          src={`${CHILD}/?embed=1`}
          basePath="/dashboard/console"
          routeMode="path"
          getToken={() => "tok_fixture"}
          label="Omega Console"
          requireGate
          requireDoc
          {...props}
        />
      </MemoryRouter>
    </ThemeProvider>
  );
  return client ? (
    <QueryClientProvider client={client}>{surface}</QueryClientProvider>
  ) : (
    surface
  );
};
const mountSurface = (props = {}) => {
  const client = new QueryClient();
  const view = render(surfaceTree(client, props));
  return {
    ...view,
    rerenderSurface: (next = props) => view.rerender(surfaceTree(client, next)),
  };
};
const frameOf = () => screen.getByTestId("omega-embed-frame");
const spyOnFrame = (frame) => {
  const posted = [];
  vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};
const deliver = (frame, data = hello(), origin = CHILD) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin,
        source: frame.contentWindow,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  observed.response = query();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("Console product availability from the real useOmegaProducts classifier", () => {
  it.each([
    ["admitted (even with a denied hosted part)", admitted, "admitted"],
    ["paused (even with a denied hosted part)", paused, "paused"],
    ["cloud with explicit hosted-part and product denial", query, "withheld"],
    [
      "loading with stale admitted data",
      () => ({ ...admitted(), isLoading: true }),
      "unknown",
    ],
    [
      "loading with stale withheld data",
      () => query({ isLoading: true }),
      "unknown",
    ],
    [
      "error with stale admitted data",
      () => ({ ...admitted(), isError: true }),
      "unknown",
    ],
    [
      "error with stale withheld data",
      () => query({ isError: true }),
      "unknown",
    ],
    [
      "missing answer while loading",
      () => query({ data: undefined, isLoading: true }),
      "unknown",
    ],
    ["missing answer", () => query({ data: undefined }), "unknown"],
    [
      "missing features",
      () => query({ data: { display_mode: "cloud" } }),
      "unknown",
    ],
    [
      "missing product denial",
      () =>
        query({
          data: {
            display_mode: "cloud",
            features: { omega_part_hosted_sandboxes: denied() },
          },
        }),
      "unknown",
    ],
    [
      "missing hosted-part denial",
      () =>
        query({
          data: {
            display_mode: "cloud",
            features: { omega_sandboxes: denied() },
          },
        }),
      "unknown",
    ],
    [
      "hosted part admitted but product denied",
      () => query({ part: { allowed: true } }),
      "unknown",
    ],
    [
      "hosted part resolver unavailable",
      () =>
        query({
          part: { allowed: false, reason_code: "RESOLVER_UNAVAILABLE" },
        }),
      "unknown",
    ],
    [
      "product resolver unavailable",
      () =>
        query({
          product: { allowed: false, reason_code: "RESOLVER_UNAVAILABLE" },
        }),
      "unknown",
    ],
    [
      "product denied without a reason",
      () => query({ product: { allowed: false } }),
      "unknown",
    ],
    [
      "hosted part denied without a reason",
      () => query({ part: { allowed: false } }),
      "unknown",
    ],
    ...["oss", "enterprise", "preview", "unknown"].map((mode) => [
      `${mode} with explicit denials`,
      () => query({ mode }),
      "unknown",
    ]),
    [
      "missing display mode",
      () => query({ data: { features: query().data.features } }),
      "unknown",
    ],
  ])(
    "posts %s exactly once after establishment",
    async (_name, observation, expected) => {
      observed.response = observation();
      mountSurface();
      const frame = frameOf();
      const posted = spyOnFrame(frame);
      expect(posted).toEqual([]);
      await deliver(frame);
      // RED at the frozen base: the real surface never posts this message.
      expect(availability(posted)).toEqual([availabilityPost(expected)]);
      expect(posted[0].message.type).toBe("embed.key");
      expect(posted[1].message.type).toBe("embed.theme");
      await deliver(frame); // same document's refill must not duplicate it
      expect(availability(posted)).toEqual([availabilityPost(expected)]);
      expect(useCapabilities).toHaveBeenCalledWith({ staleTime: 30_000 });
    },
  );

  it("posts changes in the live observation without rebuilding the session or leaking stale admission", async () => {
    const getToken = vi.fn(() => "tok_fixture");
    observed.response = admitted();
    const view = mountSurface({ getToken });
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await deliver(frame);
    expect(availability(posted)).toEqual([availabilityPost("admitted")]);

    for (const observation of [
      { ...admitted(), isLoading: true },
      query(),
      query({ isError: true }),
      paused(),
      admitted(),
    ]) {
      observed.response = observation;
      view.rerenderSurface();
      expect(frameOf()).toBe(frame);
    }
    expect(availability(posted)).toEqual(
      ["admitted", "unknown", "withheld", "unknown", "paused", "admitted"].map(
        (value) => availabilityPost(value),
      ),
    );
    expect(getToken).toHaveBeenCalledTimes(1);
    observed.response = admitted(); // new observation, same derived value
    view.rerenderSurface();
    await deliver(frame);
    expect(availability(posted)).toHaveLength(6);
  });

  it("holds pre-hello changes and sends only the latest classification after establishment", async () => {
    observed.response = admitted();
    const view = mountSurface();
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    observed.response = query();
    view.rerenderSurface();
    expect(posted).toEqual([]);
    await deliver(frame);
    expect(availability(posted)).toEqual([availabilityPost("withheld")]);
  });

  it("seeds every replacement bridge and uses its unchanged expected target origin", async () => {
    observed.response = paused();
    const view = mountSurface();
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await deliver(frame);
    expect(availability(posted)).toEqual([availabilityPost("paused")]);
    view.rerenderSurface({ expectedOrigin: OTHER });
    await deliver(frame, hello(DOC_B)); // the old origin cannot establish it
    expect(availability(posted)).toHaveLength(1);
    await deliver(frame, hello(DOC_B), OTHER);
    expect(availability(posted)).toEqual([
      availabilityPost("paused"),
      availabilityPost("paused", OTHER),
    ]);
  });

  it("resends after a document replacement, withholding updates while load suspends the old session", async () => {
    observed.response = admitted();
    const view = mountSurface();
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await deliver(frame);
    expect(availability(posted)).toEqual([availabilityPost("admitted")]);
    act(() => fireEvent.load(frame));
    observed.response = query();
    view.rerenderSurface();
    await deliver(frame); // no proof: the previous doc stays suspended
    expect(availability(posted)).toHaveLength(1);
    await deliver(frame, hello(DOC_B));
    expect(availability(posted)).toEqual([
      availabilityPost("admitted"),
      availabilityPost("withheld"),
    ]);
  });

  it("reports unknown when a generic embed host has no capabilities observation provider", async () => {
    render(surfaceTree(null));
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await deliver(frame);
    expect(availability(posted)).toEqual([availabilityPost("unknown")]);
    expect(useCapabilities).not.toHaveBeenCalled();
  });

  it("leaves Studio's existing messages and provider-free embedding unchanged", async () => {
    mountSurface({ requireDoc: false });
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await deliver(frame, { type: "embed.hello", protocol: "omega-embed/1" });
    expect(posted).toEqual([
      {
        message: { type: "embed.key", token: "tok_fixture" },
        targetOrigin: CHILD,
      },
      { message: { type: "embed.theme", mode: "light" }, targetOrigin: CHILD },
    ]);
    expect(useCapabilities).not.toHaveBeenCalled();
  });
});
