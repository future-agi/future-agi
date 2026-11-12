import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import usePausedResults from "src/sections/experiment-detail/results/usePausedResults";

const population = (rowId, done) => ({
  rows: [{ example_id: rowId }],
  summary: {
    examples_total: 4,
    runs: [{ run_id: "run-1", progress: { done, total: 4 } }],
  },
});

const initialProps = () => ({
  datasetId: "dataset-a",
  view: { runIds: ["run-1"], show: "all" },
  liveData: population("initial-row", 1),
  isError: false,
});

afterEach(cleanup);

describe("usePausedResults", () => {
  it("clears data and the paused snapshot when the live query errors", () => {
    const props = initialProps();
    const errorRenders = [];
    const { result, rerender } = renderHook(
      (currentProps) => {
        const value = usePausedResults(currentProps);
        if (currentProps.isError) errorRenders.push(value.data);
        return value;
      },
      { initialProps: props },
    );
    act(() => result.current.setPaused(props.liveData));
    const retainedLive = population("later-row", 2);
    rerender({ ...props, liveData: retainedLive });
    expect(result.current.data).toBe(props.liveData);

    // A failed refetch may retain its last successful data in the cache.
    rerender({ ...props, liveData: retainedLive, isError: true });
    expect(result.current.data).toBeNull();
    expect(result.current.paused).toBeNull();
    // Fail closed on the error render, not only after an effect runs.
    expect(errorRenders.length).toBeGreaterThan(0);
    expect(errorRenders.every((data) => data === null)).toBe(true);

    const recovered = population("recovered-row", 3);
    rerender({ ...props, liveData: recovered });
    expect(result.current.paused).toBeNull();
    expect(result.current.data).toBe(recovered);
  });

  it("returns null on error even when unpaused live data is retained", () => {
    const props = initialProps();
    const { result, rerender } = renderHook(usePausedResults, {
      initialProps: props,
    });
    expect(result.current.data).toBe(props.liveData);
    expect(result.current.paused).toBeNull();

    rerender({ ...props, isError: true });
    expect(result.current.data).toBeNull();
    expect(result.current.paused).toBeNull();
  });

  it("clears the paused snapshot when datasetId changes with the same view", () => {
    const props = initialProps();
    const { result, rerender } = renderHook(usePausedResults, {
      initialProps: props,
    });
    act(() => result.current.setPaused(props.liveData));
    expect(result.current.paused).toBe(props.liveData);

    const nextDataset = population("dataset-b-row", 2);
    rerender({ ...props, datasetId: "dataset-b", liveData: nextDataset });
    expect(result.current.paused).toBeNull();
    expect(result.current.data).toBe(nextDataset);
  });

  it("clears the paused snapshot when the view changes", () => {
    const props = initialProps();
    const { result, rerender } = renderHook(usePausedResults, {
      initialProps: props,
    });
    act(() => result.current.setPaused(props.liveData));
    expect(result.current.paused).toBe(props.liveData);

    const nextView = population("filtered-row", 2);
    rerender({
      ...props,
      view: { ...props.view, show: "regressed" },
      liveData: nextView,
    });
    expect(result.current.paused).toBeNull();
    expect(result.current.data).toBe(nextView);
  });

  it("keeps the paused snapshot while healthy live data changes", () => {
    const props = initialProps();
    const { result, rerender } = renderHook(usePausedResults, {
      initialProps: props,
    });
    act(() => result.current.setPaused(props.liveData));

    const nextLive = population("later-row", 2);
    // An equivalent view object is not a changed view.
    rerender({ ...props, view: { ...props.view }, liveData: nextLive });
    expect(result.current.paused).toBe(props.liveData);
    expect(result.current.data).toBe(props.liveData);

    // Update takes a new snapshot but keeps subsequent polling paused.
    act(() => result.current.setPaused(nextLive));
    expect(result.current.paused).toBe(nextLive);
    expect(result.current.data).toBe(nextLive);
    const newestLive = population("newest-row", 3);
    rerender({ ...props, liveData: newestLive });
    expect(result.current.data).toBe(nextLive);

    act(() => result.current.setPaused(null));
    expect(result.current.paused).toBeNull();
    expect(result.current.data).toBe(newestLive);
  });

  it("uses live data while healthy and not paused", () => {
    const props = initialProps();
    const { result, rerender } = renderHook(usePausedResults, {
      initialProps: props,
    });
    expect(result.current.paused).toBeNull();
    expect(result.current.data).toBe(props.liveData);

    const nextLive = population("later-row", 2);
    rerender({ ...props, liveData: nextLive });
    expect(result.current.paused).toBeNull();
    expect(result.current.data).toBe(nextLive);
  });

  it("refuses pause writes throughout an ongoing error", () => {
    const props = initialProps();
    const errorRenders = [];
    const { result, rerender } = renderHook(
      (currentProps) => {
        const value = usePausedResults(currentProps);
        if (currentProps.isError) errorRenders.push(value);
        return value;
      },
      { initialProps: props },
    );
    act(() => result.current.setPaused(props.liveData));
    rerender({ ...props, isError: true });
    expect(result.current.paused).toBeNull();

    // The initial clearing effect has already run; none of its dependencies
    // change when a Pause/Update handler tries to re-install retained data.
    act(() => result.current.setPaused(props.liveData));
    expect(result.current.data).toBeNull();
    expect(result.current.paused).toBeNull();
    let updaterCalled = false;
    act(() => result.current.setPaused(() => {
      updaterCalled = true;
      return props.liveData;
    }));
    expect(updaterCalled).toBe(false);
    expect(result.current.resultsBlocked).toBe(true);
    expect(errorRenders.length).toBeGreaterThan(0);
    expect(errorRenders.every((value) =>
      value.data === null && value.paused === null,
    )).toBe(true);

    const recovered = population("recovered-row", 3);
    rerender({ ...props, liveData: recovered });
    expect(result.current.paused).toBeNull();
    expect(result.current.data).toBe(recovered);
    expect(result.current.resultsBlocked).toBe(false);
  });

  it.each(["loading", "populated"])(
    "never exposes dataset A's snapshot in any dataset B render (%s)",
    (state) => {
      const props = initialProps();
      const nextData = state === "loading" ? undefined : population("b-row", 2);
      const renders = [];
      const { result, rerender } = renderHook(
        (currentProps) => {
          const value = usePausedResults(currentProps);
          renders.push({ datasetId: currentProps.datasetId, ...value });
          return value;
        },
        { initialProps: props },
      );
      act(() => result.current.setPaused(props.liveData));
      expect(result.current.paused).toBe(props.liveData);
      rerender({ ...props, datasetId: "dataset-b", liveData: nextData });
      const nextRenders = renders.filter((value) => value.datasetId === "dataset-b");
      expect(nextRenders.length).toBeGreaterThan(0);
      for (const value of nextRenders) {
        expect(value.paused).toBeNull();
        expect(value.data).toBe(nextData);
      }
      // Returning to A must not resurrect the invalidated snapshot either.
      const fresh = population("fresh-a", 4);
      rerender({ ...props, liveData: fresh });
      expect(result.current.paused).toBeNull();
      expect(result.current.data).toBe(fresh);
    },
  );

  it("never exposes the previous snapshot in any changed-view render", () => {
    const props = initialProps();
    const nextData = population("filtered-row", 2);
    const renders = [];
    const { result, rerender } = renderHook(
      (currentProps) => {
        const value = usePausedResults(currentProps);
        if (currentProps.view.show === "regressed") renders.push(value);
        return value;
      },
      { initialProps: props },
    );
    act(() => result.current.setPaused(props.liveData));
    rerender({
      ...props,
      view: { ...props.view, show: "regressed" },
      liveData: nextData,
    });
    expect(renders.length).toBeGreaterThan(0);
    for (const value of renders) {
      expect(value.paused).toBeNull();
      expect(value.data).toBe(nextData);
    }
  });
});
