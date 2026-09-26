import { describe, expect, it, vi } from "vitest";

import {
  canNavigateToNextVoiceCallDetail,
  createVoiceCallDetailCursorNavigator,
  createVoiceCallDetailRequestGuard,
  getLegacyVoiceCallNavigationTotal,
} from "../voiceCallDetailCursorNavigation";

const response = ({ rows = [], hasMore, nextCursor }) => ({
  count: rows.length,
  count_is_lower_bound: hasMore,
  total_pages: hasMore ? 2 : 1,
  current_page: 1,
  next: hasMore ? 2 : null,
  previous: null,
  results: rows,
  config: [],
  has_more: hasMore,
  next_cursor: nextCursor,
  query_complete: true,
  query_status: "complete",
});

describe("voice-call detail exact cursor navigation", () => {
  it("restarts page-one navigation once when an old backend rejects cursor fields", async () => {
    const cursorValidationError = Object.assign(
      new Error("cursor_mode: Unknown field."),
      {
        response: {
          status: 400,
          data: {
            attr: "cursor_mode",
            detail: "cursor_mode: Unknown field.",
            details: { cursor_mode: ["Unknown field."] },
          },
        },
      },
    );
    const request = vi
      .fn()
      .mockRejectedValueOnce(cursorValidationError)
      .mockResolvedValueOnce({
        results: [{ trace_id: "legacy-call-0" }, { trace_id: "legacy-call-1" }],
        next: 2,
      })
      .mockResolvedValueOnce({
        results: [{ trace_id: "legacy-call-2" }],
        next: null,
      });
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: { project_id: "project-1" },
      pageSize: 2,
    });

    await expect(navigator.loadRow(2)).resolves.toMatchObject({
      row: { trace_id: "legacy-call-2" },
      pending: false,
      terminal: true,
    });
    expect(request.mock.calls.map(([params]) => params)).toEqual([
      {
        project_id: "project-1",
        page_size: 2,
        cursor_mode: true,
        page: 1,
      },
      {
        project_id: "project-1",
        page_size: 2,
        page: 1,
      },
      {
        project_id: "project-1",
        page_size: 2,
        page: 2,
      },
    ]);
  });

  it("walks from page one to a deep row using signed cursors only", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "call-0" }, { trace_id: "call-1" }],
          hasMore: true,
          nextCursor: "signed-2",
        }),
      )
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "call-2" }, { trace_id: "call-3" }],
          hasMore: true,
          nextCursor: "signed-3",
        }),
      )
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "call-4" }],
          hasMore: false,
          nextCursor: null,
        }),
      );
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: {
        project_id: "project-1",
        filters: "[]",
        page: 99,
        cursor: "stale",
      },
      pageSize: 2,
    });

    await expect(navigator.loadRow(4)).resolves.toMatchObject({
      row: { trace_id: "call-4" },
      pending: false,
      terminal: true,
    });
    expect(request.mock.calls.map(([params]) => params)).toEqual([
      {
        project_id: "project-1",
        filters: "[]",
        page_size: 2,
        cursor_mode: true,
        page: 1,
      },
      {
        project_id: "project-1",
        filters: "[]",
        page_size: 2,
        cursor_mode: true,
        cursor: "signed-2",
      },
      {
        project_id: "project-1",
        filters: "[]",
        page_size: 2,
        cursor_mode: true,
        cursor: "signed-3",
      },
    ]);
    expect(request.mock.calls.slice(1)).toSatisfy((calls) =>
      calls.every(
        ([params]) => !("page" in params) && !("page_number" in params),
      ),
    );
  });

  it("treats an empty bounded response as a checkpoint, not end-of-data", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, nextCursor: "after-empty" }),
      )
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "real-call" }],
          hasMore: false,
          nextCursor: null,
        }),
      );
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: { project_id: "project-1" },
      pageSize: 25,
    });

    await expect(navigator.loadRow(0)).resolves.toMatchObject({
      row: { trace_id: "real-call" },
      pending: false,
      terminal: true,
    });
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("retains its signed checkpoint when an attempt reaches its safety bound", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "call-0" }],
          hasMore: true,
          nextCursor: "resume-here",
        }),
      )
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "call-1" }],
          hasMore: false,
          nextCursor: null,
        }),
      );
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: { project_id: "project-1" },
      pageSize: 1,
      maxResponsesPerAttempt: 1,
    });

    await expect(navigator.loadRow(1)).resolves.toMatchObject({
      row: null,
      pending: true,
      loadedRowCount: 1,
    });
    await expect(navigator.loadRow(1)).resolves.toMatchObject({
      row: { trace_id: "call-1" },
      pending: false,
      terminal: true,
    });
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[1][0]).toEqual({
      project_id: "project-1",
      page_size: 1,
      cursor_mode: true,
      cursor: "resume-here",
    });
  });

  it("bounds a stalled request and resumes from the same signed checkpoint", async () => {
    let stalledSignal;
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "call-0" }],
          hasMore: true,
          nextCursor: "resume-after-deadline",
        }),
      )
      .mockImplementationOnce((_params, { signal }) => {
        stalledSignal = signal;
        return new Promise((_resolve, reject) => {
          signal.addEventListener(
            "abort",
            () => {
              const error = new Error("aborted");
              error.name = "AbortError";
              reject(error);
            },
            { once: true },
          );
        });
      })
      .mockResolvedValueOnce(
        response({
          rows: [{ trace_id: "call-1" }],
          hasMore: false,
          nextCursor: null,
        }),
      );
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: { project_id: "project-1" },
      pageSize: 1,
      maxElapsedMs: 20,
    });

    await expect(navigator.loadRow(1)).resolves.toMatchObject({
      row: null,
      pending: true,
      loadedRowCount: 1,
    });
    await Promise.resolve();
    expect(stalledSignal?.aborted).toBe(true);

    await expect(navigator.loadRow(1)).resolves.toMatchObject({
      row: { trace_id: "call-1" },
      pending: false,
      terminal: true,
    });
    expect(request.mock.calls[1][0]).toEqual({
      project_id: "project-1",
      page_size: 1,
      cursor_mode: true,
      cursor: "resume-after-deadline",
    });
    expect(request.mock.calls[2][0]).toEqual(request.mock.calls[1][0]);
  });

  it("fails closed instead of falling back to numbered pagination", async () => {
    const request = vi.fn().mockResolvedValue({
      results: [{ trace_id: "call-0" }],
    });
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: { project_id: "project-1" },
      pageSize: 25,
    });

    await expect(navigator.loadRow(1)).rejects.toThrow(
      "does not support exact cursors",
    );
    expect(request).toHaveBeenCalledOnce();
    expect(request.mock.calls[0][0]).toMatchObject({
      page: 1,
      cursor_mode: true,
    });
  });

  it("rejects a missing results field instead of treating it as an empty page", async () => {
    const navigator = createVoiceCallDetailCursorNavigator({
      request: vi.fn().mockResolvedValue({
        has_more: false,
        next_cursor: null,
      }),
      baseParams: { project_id: "project-1" },
      pageSize: 25,
    });

    await expect(navigator.loadRow(0)).rejects.toThrow(
      "Voice-call list response is missing results",
    );
  });

  it("rejects legacy row identity aliases instead of guessing a trace id", async () => {
    const navigator = createVoiceCallDetailCursorNavigator({
      request: vi.fn().mockResolvedValue(
        response({
          rows: [{ id: "legacy-only-id" }],
          hasMore: false,
          nextCursor: null,
        }),
      ),
      baseParams: { project_id: "project-1" },
      pageSize: 25,
    });

    await expect(navigator.loadRow(0)).rejects.toThrow(
      "Voice-call row is missing a stable identity",
    );
  });

  it("rejects a repeated signed checkpoint instead of looping", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, nextCursor: "same" }),
      )
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, nextCursor: "same" }),
      );
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: { project_id: "project-1" },
      pageSize: 25,
    });

    await expect(navigator.loadRow(0)).rejects.toThrow(
      "repeated its continuation cursor",
    );
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("rejects a multi-cursor cycle instead of revisiting an old checkpoint", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, nextCursor: "cursor-a" }),
      )
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, nextCursor: "cursor-b" }),
      )
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, nextCursor: "cursor-a" }),
      );
    const navigator = createVoiceCallDetailCursorNavigator({
      request,
      baseParams: { project_id: "project-1" },
      pageSize: 25,
    });

    await expect(navigator.loadRow(0)).rejects.toThrow(
      "repeated its continuation cursor",
    );
    expect(request).toHaveBeenCalledTimes(3);
  });
});

describe("voice-call detail navigation state", () => {
  it("keeps Next enabled for a lower-bound total without changing exact gating", () => {
    expect(
      canNavigateToNextVoiceCallDetail({
        rowIndex: 24,
        totalCount: 25,
        totalCountIsLowerBound: true,
      }),
    ).toBe(true);
    expect(
      canNavigateToNextVoiceCallDetail({
        rowIndex: 24,
        totalCount: 25,
        totalCountIsLowerBound: false,
      }),
    ).toBe(false);
    expect(
      canNavigateToNextVoiceCallDetail({
        rowIndex: 24,
        totalCount: null,
        totalCountIsLowerBound: false,
      }),
    ).toBe(true);
    expect(
      getLegacyVoiceCallNavigationTotal({
        rowIndex: 24,
        totalCount: 25,
        totalCountIsLowerBound: true,
      }),
    ).toBe(26);
    expect(
      getLegacyVoiceCallNavigationTotal({
        rowIndex: 24,
        totalCount: 25,
        totalCountIsLowerBound: false,
      }),
    ).toBe(25);
  });

  it("invalidates older requests and every request after disposal", () => {
    const guard = createVoiceCallDetailRequestGuard();
    guard.activate();
    const first = guard.begin();
    const second = guard.begin();

    expect(guard.isCurrent(first)).toBe(false);
    expect(guard.isCurrent(second)).toBe(true);

    guard.invalidate();
    expect(guard.isCurrent(second)).toBe(false);

    const third = guard.begin();
    expect(guard.isCurrent(third)).toBe(true);
    guard.dispose();
    expect(guard.isCurrent(third)).toBe(false);

    guard.activate();
    const remounted = guard.begin();
    expect(guard.isCurrent(remounted)).toBe(true);
  });
});
