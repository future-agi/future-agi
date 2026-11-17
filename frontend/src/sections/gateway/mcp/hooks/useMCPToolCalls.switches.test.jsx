import React from "react";
import PropTypes from "prop-types";
import { act, cleanup, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderMcp } from "../testing/renderMcp";
import { useMCPToolCall, useMCPToolCalls } from "./useMCPToolCalls";

const state = vi.hoisted(() => ({ status: {}, get: vi.fn() }));
vi.mock("./useMCPConfig", () => ({ useMCPStatus: () => state.status }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: state.get } };
});
let list;
let detail;
function Probe({ gatewayId = "gw-1", enabled = true }) {
  list = useMCPToolCalls({ limit: 10 }, { gatewayId, enabled });
  detail = useMCPToolCall(46, gatewayId);
  return null;
}
Probe.propTypes = { gatewayId: PropTypes.string, enabled: PropTypes.bool };

function status(explorer) {
  state.status = { data: { features: { tool_call_explorer: explorer } } };
}
beforeEach(() => {
  status(false);
  state.get.mockReset().mockImplementation(async (url) => ({
    data: url.endsWith("46/")
      ? { result: { id: 46, arguments: { stored: true } } }
      : { results: [{ id: 46 }], next: "https://example.com/?cursor=next" },
  }));
});
afterEach(cleanup);

it.each([false, undefined, null, 1, "true"])(
  "OFF/invalid %s blocks manual list pagination and detail refetch",
  async (value) => {
    status(value);
    renderMcp(<Probe />);
    await act(async () => {
      await list.refetch();
      await list.fetchNextPage();
      await detail.refetch();
    });
    expect(state.get).not.toHaveBeenCalled();
    expect(list.data).toBeUndefined();
    expect(detail.data).toBeUndefined();
  },
);
it("ON permits list, detail and manual pagination", async () => {
  status(true);
  renderMcp(<Probe />);
  await waitFor(() => expect(detail.data?.id).toBe(46));
  await waitFor(() =>
    expect(list.data?.pages[0].results).toEqual([{ id: 46 }]),
  );
  await act(async () => {
    await list.fetchNextPage();
  });
  expect(state.get).toHaveBeenCalledWith(
    "/agentcc/mcp/tool-calls/",
    expect.objectContaining({ params: { limit: 10, cursor: "next" } }),
  );
});
it("ON->OFF masks cached rows/detail and manual callbacks cannot bypass OFF", async () => {
  status(true);
  const view = renderMcp(<Probe />);
  await waitFor(() => expect(detail.data?.id).toBe(46));
  await waitFor(() => expect(list.data?.pages).toHaveLength(1));
  const requests = state.get.mock.calls.length;
  status(false);
  view.rerender(<Probe />);
  expect(list.data).toBeUndefined();
  expect(detail.data).toBeUndefined();
  await act(async () => {
    await list.refetch();
    await list.fetchNextPage();
    await detail.refetch();
  });
  expect(state.get).toHaveBeenCalledTimes(requests);
});
it.each([{ isError: true }, { isLoading: true }])(
  "unavailable status %j cannot use stale ON",
  async (extra) => {
    status(true);
    Object.assign(state.status, extra);
    renderMcp(<Probe />);
    await act(async () => {
      await list.refetch();
      await detail.refetch();
    });
    expect(state.get).not.toHaveBeenCalled();
  },
);
it("a missing gateway never requests calls even with cached ON status", async () => {
  status(true);
  renderMcp(<Probe gatewayId="" />);
  await act(async () => {
    await list.refetch();
    await detail.refetch();
  });
  expect(state.get).not.toHaveBeenCalled();
});
it("the caller's list disable still applies with explorer ON", async () => {
  status(true);
  renderMcp(<Probe enabled={false} />);
  await waitFor(() => expect(detail.data?.id).toBe(46));
  await act(async () => {
    await list.refetch();
    await list.fetchNextPage();
  });
  expect(state.get.mock.calls.every(([url]) => url.endsWith("/46/"))).toBe(
    true,
  );
});
