import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import TracingTestMode, {
  buildTracingPreviewListParams,
} from "./TracingTestMode";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: { ...actual.default, get: axiosGetMock, post: vi.fn() },
  };
});

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, useSnackbar: () => ({ enqueueSnackbar: vi.fn() }) };
});

describe("buildTracingPreviewListParams", () => {
  it("does not send unsupported interval params to observe list endpoints", () => {
    const params = buildTracingPreviewListParams({
      selectedProjectId: "project-1",
      effectiveFilters: [
        {
          column_id: "created_at",
          filter_config: {
            filter_type: "datetime",
            filter_op: "between",
            filter_value: [
              "2025-01-01T00:00:00.000Z",
              "2026-01-01T00:00:00.000Z",
            ],
          },
        },
      ],
    });

    expect(params).toEqual({
      project_id: "project-1",
      page_number: 0,
      page_size: 50,
      filters: JSON.stringify([
        {
          column_id: "created_at",
          filter_config: {
            filter_type: "datetime",
            filter_op: "between",
            filter_value: [
              "2025-01-01T00:00:00.000Z",
              "2026-01-01T00:00:00.000Z",
            ],
          },
        },
      ]),
    });
    expect(params).not.toHaveProperty("interval");
  });
});

// The saved mapping and the trace field list are separate queries with no
// ordering guarantee, so auto-map can land first. These load real field data
// so it actually runs.

const testTheme = createTheme({ palette: palette("light") });

// One span row whose keys name-match the template's variables, so auto-map has
// something to match on — this is what makes the race reachable at all.
const SPAN_ROW = { question: "hi there", answer: "hello back" };

const renderTracing = (props, ref) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  const ui = (extra = {}) => (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={testTheme}>
        <TracingTestMode
          ref={ref}
          templateId="tmpl-1"
          model="turing_large"
          variables={["question", "answer"]}
          initialProjectId="proj-1"
          {...props}
          {...extra}
        />
      </ThemeProvider>
    </QueryClientProvider>
  );

  const view = render(ui());
  return { ...view, rerenderWith: (extra) => view.rerender(ui(extra)) };
};

describe("TracingTestMode → saved mapping vs auto-map", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockImplementation(async (url) => {
      if (typeof url === "string" && url.includes("list_project_ids")) {
        return {
          data: { result: { projects: [{ id: "proj-1", name: "P1" }] } },
        };
      }
      return {
        data: {
          result: {
            config: [],
            table: [SPAN_ROW],
            metadata: { total_rows: 1 },
          },
        },
      };
    });
  });

  it("keeps the saved mapping when auto-map has already filled the same key", async () => {
    const ref = React.createRef();
    // The version query hasn't resolved yet — exactly the cold-load ordering
    // where auto-map wins the race.
    const { rerenderWith } = renderTracing({ initialMapping: null }, ref);

    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping).toEqual({
        question: "question",
        answer: "answer",
      });
    });

    // The saved mapping arrives late, and points at a nested path auto-map
    // would never have guessed.
    rerenderWith({
      initialMapping: { question: "attributes.llm.input_messages.0.content" },
    });

    await waitFor(() => {
      const { mapping } = ref.current.getMappingState();
      // The seeded value wins for its own key...
      expect(mapping.question).toBe("attributes.llm.input_messages.0.content");
      // ...and auto-map's work on the OTHER key survives. The all-or-nothing
      // guard failed both halves: it kept `question: "question"` and threw the
      // saved value away entirely.
      expect(mapping.answer).toBe("answer");
    });
  });

  it("re-seeds from a new version without inheriting the previous one's keys", async () => {
    const ref = React.createRef();
    const { rerenderWith } = renderTracing(
      { seedKey: "v1", initialMapping: { question: "attributes.v1.value" } },
      ref,
    );

    await waitFor(() => {
      expect(ref.current?.getMappingState?.().mapping.question).toBe(
        "attributes.v1.value",
      );
    });

    rerenderWith({
      seedKey: "v2",
      initialMapping: { answer: "attributes.v2.value" },
    });

    await waitFor(() => {
      const { mapping } = ref.current.getMappingState();
      expect(mapping.answer).toBe("attributes.v2.value");
      // v1's explicit choice is gone. Auto-map may re-derive `question` from
      // the field list, but it must not still be v1's saved path.
      expect(mapping.question).not.toBe("attributes.v1.value");
    });
  });
});
