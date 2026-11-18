/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { useController } from "react-hook-form";

const markers = {
  name: "SYNTHETIC_LABEL_NAME_a132",
  description: "SYNTHETIC_LABEL_DESCRIPTION_b243 person@example.test",
  option: "SYNTHETIC_OPTION_c354",
  placeholder: "SYNTHETIC_PLACEHOLDER_d465",
};

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    labels: [],
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post, put: mocks.put },
  endpoints: {
    project: {
      getAnnotationsForSpanId: () => "/span-annotations/",
      createLabel: () => "/labels/",
      updateLabel: (id) => `/labels/${id}/`,
    },
  },
}));
vi.mock("src/api/annotation/annotation", () => ({
  useAnnotationLabels: () => ({
    labels: mocks.labels,
    isPending: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  }),
}));
vi.mock("../useSelectedNode", () => ({
  useSelectedNode: () => ({ selectedNode: null }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "user-1" } }),
}));
vi.mock("../useDeleteAnnotationLabel", () => ({
  useDeleteAnnotationLabel: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: function TestTextField({ control, fieldName, label }) {
    const { field } = useController({ control, name: fieldName });
    return (
      <label>
        {label || fieldName}
        <input
          aria-label={label || fieldName}
          {...field}
          value={field.value ?? ""}
        />
      </label>
    );
  },
}));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: ({ control, fieldName, onChange, options }) => {
    const { field } = useController({ control, name: fieldName });
    return (
      <select
        aria-label="Annotation Type"
        {...field}
        onChange={(event) => {
          field.onChange(event);
          onChange?.(event);
        }}
      >
        <option value="">Select</option>
        {options.map(({ value }) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
    );
  },
}));
vi.mock(
  "src/sections/develop-detail/Annotations/CreateEditLabel/ConditionalLabelForm",
  () => ({
    default: function TestSettingsForm({ control }) {
      const { field } = useController({ control, name: "settings" });
      return (
        <button
          type="button"
          onClick={() =>
            field.onChange(
              field.value?.options
                ? {
                    options: [{ id: "option-1", value: markers.option }],
                    multiChoice: false,
                  }
                : {
                    placeholder: markers.placeholder,
                    minLength: "1",
                    maxLength: "30",
                  },
            )
          }
        >
          Fill label settings
        </button>
      );
    },
  }),
);
vi.mock(
  "src/sections/develop-detail/Annotations/CreateEditLabel/LabelPreview",
  () => ({
    default: () => null,
  }),
);
vi.mock(
  "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldWrapper",
  () => ({
    default: ({ onEditLabelClick }) =>
      onEditLabelClick ? (
        <button onClick={onEditLabelClick}>Edit label</button>
      ) : null,
  }),
);
vi.mock(
  "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldSkeleton",
  () => ({
    default: () => null,
  }),
);
vi.mock("../ConfirmAnnotationDelete", () => ({ default: () => null }));

import AnnotateDrawer from "../AnnotateDrawer";
import AddAnnotationsDrawer from "../add-annotations-drawer";
import { Events } from "src/utils/Mixpanel";

const renderDrawer = (Component, props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const theme = createTheme({
    palette: { status: { info: { fg: "#123456" } } },
  });
  return render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <Component open projectId="project-1" onClose={vi.fn()} {...props} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
};

const submitLabel = async (type) => {
  fireEvent.change(screen.getByRole("textbox", { name: "Label Name" }), {
    target: { value: markers.name },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "description" }), {
    target: { value: markers.description },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "Annotation Type" }), {
    target: { value: type },
  });
  fireEvent.click(screen.getByRole("button", { name: "Fill label settings" }));
  const save = screen.getByRole("button", { name: "save-edited-annotation" });
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
};

const expectTrackedFields = (type, expected) => {
  expect(mocks.mixpanel.track).toHaveBeenCalledTimes(1);
  expect(mocks.mixpanel.track.mock.calls[0][0]).toBe(
    Events.projectAnnotation[type],
  );
  expect(mocks.mixpanel.track.mock.calls[0][1]).toEqual({ click: expected });
  const calls = JSON.stringify(mocks.mixpanel.track.mock.calls);
  Object.values(markers).forEach((marker) =>
    expect(calls).not.toContain(marker),
  );
  expect(calls).not.toContain("person@example.test");
};

describe("annotation label events from drawers", () => {
  beforeEach(() => {
    mocks.labels = [];
    mocks.mixpanel.track.mockClear();
    mocks.get
      .mockReset()
      .mockResolvedValue({ data: { result: { annotations: [], notes: [] } } });
    mocks.post.mockReset().mockResolvedValue({ data: { id: "new-label" } });
    mocks.put.mockReset().mockResolvedValue({ data: { id: "edited-label" } });
  });

  it("sends the label type and option count when a categorical label is created", async () => {
    renderDrawer(AddAnnotationsDrawer);
    fireEvent.click(screen.getByRole("button", { name: /create new label/i }));
    await submitLabel("categorical");
    expect(mocks.post.mock.calls[0][1]).toEqual({
      name: markers.name,
      description: markers.description,
      type: "categorical",
      project: "project-1",
      settings: {
        options: [{ label: markers.option }],
        multi_choice: false,
        rule_prompt: "",
        auto_annotate: false,
        strategy: null,
      },
    });
    expectTrackedFields("categorical", {
      type: "categorical",
      has_description: true,
      multi_choice: false,
      option_count: 1,
    });
  });

  it("sends the label type and text settings when a text label is created", async () => {
    renderDrawer(AnnotateDrawer, { voiceObserveSpanId: "span-1" });
    await screen.findByRole("button", { name: "Create Label" });
    fireEvent.click(screen.getByRole("button", { name: "Create Label" }));
    await submitLabel("text");
    expect(mocks.post.mock.calls[0][1]).toEqual({
      name: markers.name,
      description: markers.description,
      type: "text",
      project: "project-1",
      settings: {
        placeholder: markers.placeholder,
        min_length: 1,
        max_length: 30,
      },
    });
    expectTrackedFields("text", {
      type: "text",
      has_description: true,
      min_length: 1,
      max_length: 30,
      has_placeholder: true,
    });
  });

  it("sends text settings when an existing label is updated", async () => {
    mocks.labels = [
      {
        id: "label-1",
        name: "Existing label",
        type: "text",
        description: "",
        settings: { placeholder: "Old", minLength: "1", maxLength: "25" },
      },
    ];
    renderDrawer(AnnotateDrawer, { voiceObserveSpanId: "span-1" });
    fireEvent.click(await screen.findByRole("button", { name: "Edit label" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Label Name" }), {
      target: { value: markers.name },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "description" }), {
      target: { value: markers.description },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Fill label settings" }),
    );
    const save = screen.getByRole("button", { name: "save-edited-annotation" });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(mocks.put.mock.calls[0]).toEqual([
      "/labels/label-1/",
      {
        name: markers.name,
        description: markers.description,
        type: "text",
        project: "project-1",
        settings: {
          placeholder: markers.placeholder,
          min_length: 1,
          max_length: 30,
        },
      },
    ]);
    expectTrackedFields("text", {
      type: "text",
      has_description: true,
      min_length: 1,
      max_length: 30,
      has_placeholder: true,
    });
  });
});
