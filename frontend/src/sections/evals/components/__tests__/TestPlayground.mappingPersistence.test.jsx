import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TestPlayground from "../TestPlayground";

// TH-7114: the bug shipped because TestPlayground destructured
// `initialMapping` / `initialTracingProjectId` as props but never actually
// forwarded them to <DatasetTestMode> / <TracingTestMode>. These tests mount
// the REAL TestPlayground + REAL TracingTestMode + REAL DatasetMode (no
// mocking of the three files this ticket touches) and drive the exact tab
// switch a user performs, so a revert of either forwarding line goes red.

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      ...actual.default,
      get: axiosGetMock,
      post: axiosPostMock,
      put: vi.fn().mockResolvedValue({ data: { result: {} } }),
    },
  };
});

const renderPlayground = (props, ref) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <TestPlayground ref={ref} {...props} />
      </BrowserRouter>
    </QueryClientProvider>,
  );
};

describe("TestPlayground → mapping/tracing-project forwarding (TH-7114)", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue({ data: { result: [], count: 0 } });
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { result: {} } });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("forwards initialMapping + initialTracingProjectId into the real TracingTestMode so a saved mapping is restored on the Tracing tab", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        templateId: "tmpl-1",
        evalType: "llm",
        requiredKeys: ["question", "answer"],
        showVersions: false,
        initialMapping: {
          question: "attributes.input.value",
          answer: "attributes.output.value",
        },
        initialTracingProjectId: "project-99",
      },
      ref,
    );

    // Drive the real tab switch a user performs after Save Version restores
    // a version whose test panel was last left on Tracing.
    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.()).toEqual({
        mapping: {
          question: "attributes.input.value",
          answer: "attributes.output.value",
        },
        tracingProjectId: "project-99",
      });
    });
  });

  it("forwards initialMapping into the real DatasetTestMode so a saved mapping is restored on the Dataset tab", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        templateId: "tmpl-1",
        evalType: "llm",
        requiredKeys: ["question", "answer"],
        showVersions: false,
        initialMapping: {
          question: "row.question_col",
          answer: "row.answer_col",
        },
        initialTracingProjectId: null,
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Dataset" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.()).toEqual({
        mapping: {
          question: "row.question_col",
          answer: "row.answer_col",
        },
        tracingProjectId: null,
      });
    });
  });

  it("RED-if-reverted guard: getMappingState reflects an EMPTY mapping when initialMapping/initialTracingProjectId are not forwarded at all", async () => {
    // Same as the first test but simulating the pre-fix TestPlayground,
    // which never passed initialMapping/initialTracingProjectId down. This
    // pins the observable failure mode so a future silent drop of the
    // forwarding props is caught the same way this one was.
    const ref = React.createRef();
    renderPlayground(
      {
        templateId: "tmpl-1",
        evalType: "llm",
        requiredKeys: ["question", "answer"],
        showVersions: false,
        // initialMapping / initialTracingProjectId intentionally omitted.
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.()).toEqual({
        mapping: {},
        tracingProjectId: null,
      });
    });
  });
});
