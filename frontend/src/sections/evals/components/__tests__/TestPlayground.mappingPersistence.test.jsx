import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The test modes read app-specific palette extensions (e.g. `amber`, used to
// outline an unmapped variable) that a bare createTheme() doesn't have.
import { palette } from "src/theme/palette";

import TestPlayground from "../TestPlayground";

const testTheme = createTheme({ palette: palette("light") });

// Mounts the REAL TestPlayground + TracingTestMode + DatasetTestMode and
// drives the exact tab switch a user performs, so a revert of any forwarding
// line goes red.

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

  const ui = (extra = {}) => (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={testTheme}>
        <BrowserRouter>
          <TestPlayground ref={ref} {...props} {...extra} />
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );

  const view = render(ui());
  return { ...view, rerenderWith: (extra) => view.rerender(ui(extra)) };
};

const BASE = {
  templateId: "tmpl-1",
  evalType: "llm",
  showVersions: false,
  instructions: "Judge {{question}} against {{answer}}",
};

describe("TestPlayground → mapping/tracing-project forwarding", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue({ data: { result: [], count: 0 } });
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { result: {} } });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("forwards the tracing bucket + project into the real TracingTestMode so a saved mapping is restored on the Tracing tab", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        ...BASE,
        initialMapping: {
          tracing: {
            question: "attributes.input.value",
            answer: "attributes.output.value",
          },
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
          tracing: {
            question: "attributes.input.value",
            answer: "attributes.output.value",
          },
          dataset: {},
        },
        tracingProjectId: "project-99",
      });
    });
  });

  it("forwards the dataset bucket into the real DatasetTestMode so a saved mapping is restored on the Dataset tab", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        ...BASE,
        initialMapping: {
          dataset: { question: "col_question", answer: "col_answer" },
        },
        initialTracingProjectId: null,
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Dataset" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.()).toEqual({
        mapping: {
          tracing: {},
          dataset: { question: "col_question", answer: "col_answer" },
        },
        tracingProjectId: null,
      });
    });
  });

  // Column ids are not trace-field paths — the Tracing picker used to render
  // values it can never resolve.
  it("does NOT seed the Tracing tab from a dataset-only mapping", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        ...BASE,
        initialMapping: {
          dataset: { question: "col_question", answer: "col_answer" },
        },
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping.tracing).toEqual({});
    });
  });

  it("does NOT seed the Dataset tab from a tracing-only mapping", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        ...BASE,
        initialMapping: {
          tracing: { question: "attributes.input.value" },
        },
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Dataset" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping.dataset).toEqual({});
    });
  });

  // Three of the five tabs mount neither test mode; a save from one of those
  // used to write nothing and still toast success.
  it("still reports the Tracing mapping after the user switches to a tab that has none", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        ...BASE,
        initialMapping: {
          tracing: { question: "attributes.input.value" },
        },
        initialTracingProjectId: "project-99",
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));
    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping.tracing).toEqual({
        question: "attributes.input.value",
      });
    });

    // Simulation mounts neither test mode — the pre-fix handle returned null
    // here and Save Version wrote nothing while still toasting success.
    fireEvent.click(screen.getByRole("tab", { name: "Simulation" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.()).toEqual({
        mapping: {
          tracing: { question: "attributes.input.value" },
          dataset: {},
        },
        tracingProjectId: "project-99",
      });
    });
  });

  // A renamed variable used to leave the old key mapped forever while the new
  // one read as unmapped, which keeps Test disabled and looks like a broken
  // restore.
  it("drops mapping entries for variables the template no longer has", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        ...BASE,
        instructions: "Judge {{question}} against {{answer}}",
        initialMapping: {
          tracing: {
            question: "attributes.input.value",
            answer: "attributes.output.value",
            removed_variable: "attributes.stale.value",
          },
        },
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping.tracing).toEqual({
        question: "attributes.input.value",
        answer: "attributes.output.value",
      });
    });
  });

  // Fail-safe: while the variable set is still unknown (no instructions parsed
  // yet), pruning would wipe a perfectly good saved mapping.
  it("keeps the saved mapping intact when the variable set is not known yet", async () => {
    const ref = React.createRef();
    renderPlayground(
      {
        ...BASE,
        instructions: "",
        initialMapping: {
          tracing: { question: "attributes.input.value" },
        },
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping.tracing).toEqual({
        question: "attributes.input.value",
      });
    });
  });

  // Remounting the panel to re-seed also dropped the active tab, the dataset
  // selection and any test in flight.
  it("re-seeds from the newly viewed version in place, keeping the active tab", async () => {
    const ref = React.createRef();
    const { rerenderWith } = renderPlayground(
      {
        ...BASE,
        seedKey: "v1",
        initialMapping: { tracing: { question: "attributes.v1.value" } },
      },
      ref,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));
    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping.tracing).toEqual({
        question: "attributes.v1.value",
      });
    });

    rerenderWith({
      seedKey: "v2",
      initialMapping: { tracing: { answer: "attributes.v2.value" } },
    });

    await waitFor(() => {
      // v1's key is gone, not merged in — the previous version's mapping is
      // not this version's.
      expect(ref.current?.getMappingState?.().mapping.tracing).toEqual({
        answer: "attributes.v2.value",
      });
    });
    // ...and the user is still on the tab they were working in.
    expect(screen.getByRole("tab", { name: "Tracing" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("RED-if-reverted guard: getMappingState reflects an EMPTY mapping when nothing is forwarded at all", async () => {
    // Same as the first test but simulating the pre-fix TestPlayground,
    // which never passed initialMapping/initialTracingProjectId down. This
    // pins the observable failure mode so a future silent drop of the
    // forwarding props is caught the same way this one was.
    const ref = React.createRef();
    renderPlayground({ ...BASE }, ref);

    fireEvent.click(screen.getByRole("tab", { name: "Tracing" }));

    await waitFor(() => {
      expect(ref.current?.getMappingState?.()).toEqual({
        mapping: { tracing: {}, dataset: {} },
        tracingProjectId: null,
      });
    });
  });
});
