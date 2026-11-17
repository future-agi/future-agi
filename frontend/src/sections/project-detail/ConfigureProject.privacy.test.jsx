import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import axios, { endpoints } from "src/utils/axios";

const state = vi.hoisted(() => ({ project: null }));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: state.project }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: { project: { updateProject: "/tracer/project/update_project_name/" } },
}));
vi.mock("react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/hook-form", () => ({ RHFSlider: () => null }));
vi.mock("./DeleteProject", () => ({ default: () => null }));

import ConfigureProject from "./ConfigureProject";

const theme = createTheme({ palette: palette("light") });

function renderSettings(module = "observe") {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <ConfigureProject open id="test-project" module={module} onClose={vi.fn()} refreshGrid={vi.fn()} />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { status: true } });
  state.project = {
    id: "test-project",
    name: "Test project",
    sampling_rate: 0,
    pii_redaction_enabled: true,
    pii_redaction_mode: "hide_io",
  };
});

describe("ConfigureProject privacy", () => {
  it("renders the saved privacy switch and mode for newly ingested traces", async () => {
    renderSettings();
    expect(await screen.findByRole("checkbox", { name: "Redact PII in new traces" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Redaction mode" })).toHaveTextContent("Hide inputs and outputs");
    expect(screen.getByText(/applies to newly ingested traces/i)).toBeInTheDocument();
  });

  it("defaults to disabled masking when a project has no privacy settings", async () => {
    delete state.project.pii_redaction_enabled;
    delete state.project.pii_redaction_mode;
    renderSettings();
    expect(await screen.findByRole("checkbox", { name: "Redact PII in new traces" })).not.toBeChecked();
    expect(screen.getByRole("combobox", { name: "Redaction mode" })).toHaveTextContent("Mask PII");
    expect(screen.getByRole("combobox", { name: "Redaction mode" })).toHaveAttribute("aria-disabled", "true");
  });

  it("sends the changed switch and selected mode through the existing update call", async () => {
    state.project.pii_redaction_enabled = false;
    state.project.pii_redaction_mode = "mask_pii";
    renderSettings();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Redact PII in new traces" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Redaction mode" }));
    fireEvent.click(await screen.findByRole("option", { name: "Hide inputs and outputs" }));
    const update = screen.getByRole("button", { name: "update-project" });
    await waitFor(() => expect(update).toBeEnabled());
    fireEvent.click(update);
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith(endpoints.project.updateProject, {
      project_id: "test-project",
      name: "Test project",
      sampling_rate: 0,
      sampling_contact_attribute: "",
      pii_redaction_enabled: true,
      pii_redaction_mode: "hide_io",
    }));
  });

  it("sends false when redaction is turned off without losing the mode", async () => {
    renderSettings();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Redact PII in new traces" }));
    const update = screen.getByRole("button", { name: "update-project" });
    await waitFor(() => expect(update).toBeEnabled());
    fireEvent.click(update);
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith(endpoints.project.updateProject, expect.objectContaining({
      pii_redaction_enabled: false,
      pii_redaction_mode: "hide_io",
    })));
  });
});
