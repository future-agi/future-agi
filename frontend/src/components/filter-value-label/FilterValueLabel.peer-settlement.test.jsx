// Test-only overlay. This file changes only the HTTP adapter. No QueryClient,
// hook, component, scheduler, clock or React notification machinery is mocked.
import React, { Profiler } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { expect, it } from "vitest";
import http from "src/utils/axios";
import { useResolvedFilterOptions } from "./useResolvedFilterOptions";
import FilterValueLabel from "./FilterValueLabel";

const filter = { id: "reviewer", type: "annotation", value: ["saved-id"] };
const C = "C";
const first = { value: "other-id", label: "Other reviewer" };
const deferred = () => {
  let reject;
  const promise = new Promise((_, no) => { reject = no; });
  return { promise, reject };
};

function Peer({ mode, log }) {
  const client = useQueryClient();
  const q = useResolvedFilterOptions(filter, "traces");
  const state = q.isFetchNextPageError ? "continuation-error" : q.isError ? "ordinary-error" : "ok";
  const refetch = () => {
    log("peer.refetch");
    return client.refetchQueries({ type: "active" });
  };
  const next = () => {
    log("peer.fetchNextPage");
    // TanStack's default (throwOnError:false) resolves to the observer result on error.
    void q.fetchNextPage().then((settled) => {
      log("peer.settled", { status: settled.status, nextError: settled.isFetchNextPageError });
      if (mode === "direct") return refetch();
      return undefined;
    });
  };
  return <><output data-peer>{`${q.fetchStatus}:${q.continuationKey}:${state}`}</output>
    <button onClick={next}>Peer load</button><button onClick={refetch}>Peer refetch</button></>;
}

it.each(["allow-idle-commit", "direct"])("never reads a failed peer cursor twice (%s)", async (mode) => {
  const client = new QueryClient();
  const savedAdapter = http.defaults.adapter;
  const events = [];
  const cursors = [];
  const log = (event, facts = {}) => events.push({ seq: events.length + 1, event, ...facts });
  const root = document.createElement("div");
  document.body.appendChild(root);
  const dom = () => ({
    peer: root.querySelector("[data-peer]")?.textContent ?? null,
    chip: root.querySelector("[data-chip] .MuiSkeleton-root") ? "skeleton" :
      (root.querySelector("[data-chip] .filter-value-name")?.textContent ?? "absent"),
  });
  const endPeer = deferred(), endRefetch = deferred(), endDuplicate = deferred();
  const unsubscribe = client.getQueryCache().subscribe(({ type, query, action }) => {
    if (type !== "updated") return;
    const s = query.state;
    log("cache", { action: action.type, status: s.status, fetch: s.fetchStatus,
      direction: s.fetchMeta?.fetchMore?.direction ?? null, errors: s.errorUpdateCount });
  });
  http.defaults.adapter = (config) => {
    const body = typeof config.data === "string" ? JSON.parse(config.data) : config.data;
    const cursor = body?.cursor ?? config.params?.cursor ?? null;
    cursors.push(cursor);
    log("http", { call: cursors.length, cursor, method: config.method, path: config.url });
    if (config.url !== "/tracer/dashboard/filter_values/" || config.method !== "post") throw Error("Unexpected transport route");
    if (cursors.length === 1 && cursor === null) return Promise.resolve({ config, status: 200,
      statusText: "OK", headers: {}, data: { result: { values: [first], has_more: true, next_cursor: C } } });
    if (cursors.length === 2 && cursor === C) return endPeer.promise;
    if (cursors.length === 3 && cursor === null) return endRefetch.promise;
    if (cursors.length === 4 && cursor === C) return endDuplicate.promise;
    throw Error("Unexpected transport sequence");
  };
  const tree = (showChip) => <QueryClientProvider client={client}>
    <Profiler id="pair" onRender={(_, phase) => log("commit", { phase, ...dom() })}>
      <Peer mode={mode} log={log} />
      {showChip && <div data-chip><FilterValueLabel filter={filter} source="traces" /></div>}
    </Profiler>
  </QueryClientProvider>;
  let view;
  const wait = (check) => waitFor(check, { timeout: 3000, container: root });
  const failTransport = (d, name) => {
    // Resolve the transport in a normal task; no act around the settlement, no fake timers.
    setTimeout(() => { log("reject:" + name); d.reject(new Error(name)); }, 0);
  };
  try {
    view = render(tree(false), { container: root });
    await wait(() => expect(dom().peer).toBe("idle:C:ok"));
    fireEvent.click(view.getByText("Peer load"));
    await wait(() => expect(cursors).toEqual([null, C]));
    // Both transport calls precede the chip's first mount. Its observer joins
    // the shared nonempty cached query, without a query-key or selection change.
    view.rerender(tree(true));
    await wait(() => expect(dom()).toEqual({ peer: "fetching:C:ok", chip: "skeleton" }));
    expect(client.getQueryCache().getAll()).toHaveLength(1);
    expect(cursors).toEqual([null, C]);
    failTransport(endPeer, "peer-C");
    if (mode === "allow-idle-commit") {
      await wait(() => expect(dom()).toEqual({ peer: "idle:C:continuation-error", chip: "saved-id" }));
      fireEvent.click(view.getByText("Peer refetch"));
    }
    await wait(() => expect(cursors).toEqual([null, C, null]));
    await wait(() => expect(dom().peer).toBe("fetching:C:ordinary-error"));
    const start = events.find(e => e.event === "peer.refetch").seq;
    const sawIdle = events.some(e => e.event === "commit" && e.seq < start && e.peer === "idle:C:continuation-error");
    expect(sawIdle).toBe(mode === "allow-idle-commit");
    expect(events.filter(e => e.event === "peer.settled")).toMatchObject([{status: "error", nextError: true}]);
    failTransport(endRefetch, "ordinary-page-one");
    // Wait for a positive observation on either path, never infer a duplicate
    // or a stuck chip from time passing. Record the UI before and after
    // settling any genuinely dispatched fourth request.
    await wait(() => expect(cursors.length >= 4 ||
      (dom().peer === "idle:C:ordinary-error" && dom().chip === "saved-id")).toBe(true));
    if (cursors.length === 4) {
      await wait(() => expect(dom().chip).toBe("skeleton"));
      log("duplicate-pending", dom());
      failTransport(endDuplicate, "duplicate-C");
      await wait(() => expect(dom()).toEqual({ peer: "idle:C:continuation-error", chip: "saved-id" }));
    }
    log("settled-ui", dom());
    expect(cursors, "chip reissued peer-owned C after ordinary refetch failed").toEqual([null, C, null]);
    expect(dom().chip).toBe("saved-id");
  } finally {
    console.info("PICKER_TRACE " + JSON.stringify({ mode, cursors, events }));
    view?.unmount(); root.remove(); unsubscribe(); client.clear(); http.defaults.adapter = savedAdapter;
  }
});
