import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";
import { downloadVersionZip, useArchiveSkill, useSkill } from "src/api/skills";
import { enqueueSnackbar } from "notistack";
import VersionsTab from "../versions/VersionsTab";
import SkillDetailView from "./SkillDetailView";

vi.mock("src/api/skills", () => ({
  useSkill: vi.fn(),
  useArchiveSkill: vi.fn(),
  downloadVersionZip: vi.fn(),
  getSkillErrorCode: (error) => error?.code,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/config-global", () => ({ HOST_API: "https://api.example.test" }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./FilesTab", () => ({ default: () => <p>Published file viewer</p> }));
vi.mock("./UsageTab", () => ({ default: () => <p>Usage view</p> }));
vi.mock("./UseSkillPanel", () => ({ default: () => <p>Install commands</p> }));
vi.mock("../versions/VersionsTab", () => ({
  default: vi.fn(() => <p>Version history</p>),
}));
const skill = {
  id: "skill-a",
  name: "support-policy",
  description: "Use when answering support policy questions.",
  latestVersion: 4,
  labels: [{ name: "production", version: 2 }],
  hasDraft: true,
  drafts: [
    { isMine: true, user: { name: "Me" } },
    { isMine: false, user: { name: "Avery" } },
  ],
};
const theme = createTheme({
  palette: dsPalette("light"),
  typography: dsTypography,
});
const success = (data) => ({
  data,
  isPending: false,
  isError: false,
  refetch: vi.fn(),
});
function Location() {
  const location = useLocation();
  return (
    <output aria-label="URL">
      {location.pathname}
      {location.search}
    </output>
  );
}
function show(search = "") {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[`/dashboard/skills/skill-a${search}`]}>
        <Routes>
          <Route
            path="/dashboard/skills/:skillId"
            element={<SkillDetailView />}
          />
          <Route path="/dashboard/skills" element={<p>Skills library</p>} />
        </Routes>
        <Location />
      </MemoryRouter>
    </ThemeProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  useSkill.mockReturnValue(success(skill));
  useArchiveSkill.mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: false,
  });
  downloadVersionZip.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});
afterEach(cleanup);

describe("Skill detail route", () => {
  it("reads the route ID, shows production and own/other draft actions", () => {
    show();
    expect(useSkill).toHaveBeenCalledWith("skill-a");
    expect(
      screen.getByRole("heading", { name: "support-policy" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/production.*v2/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Continue draft" }),
    ).toHaveAttribute("href", "/dashboard/skills/skill-a/edit");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Avery has a draft in progress",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("Me");
  });
  it("labels the editor action Edit when there is no own draft", () => {
    useSkill.mockReturnValue(
      success({ ...skill, hasDraft: false, drafts: [] }),
    );
    show();
    expect(screen.getByRole("link", { name: "Edit" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("restores the URL tab and preserves deep links when changing it", () => {
    show("?tab=versions&version=2&path=references%2Fpolicy.md&source=shared");
    expect(screen.getByRole("tab", { name: "Versions" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(VersionsTab).toHaveBeenCalledWith(
      expect.objectContaining({ skill }),
      expect.anything(),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    expect(screen.getByText("Usage view")).toBeInTheDocument();
    expect(screen.getByLabelText("URL")).toHaveTextContent(
      "tab=usage&version=2&path=references%2Fpolicy.md&source=shared",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Use skill", exact: true }),
    );
    expect(screen.getByText("Install commands")).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Use", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
  });
  it("falls back to Files for an unknown tab", () => {
    show("?tab=unknown");
    expect(screen.getByText("Published file viewer")).toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      "skill-tab-files",
    );
  });
  it.each([
    ["?version=2", 2],
    ["?version=label:production", 2],
    ["", 4],
  ])(
    "downloads the selected immutable version for %s",
    async (search, number) => {
      show(search);
      fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Download ZIP" }));
      await waitFor(() =>
        expect(downloadVersionZip).toHaveBeenCalledWith("skill-a", number),
      );
    },
  );
  it("disables downloads for malformed or missing selectors instead of falling back", () => {
    show("?version=9007199254740993");
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    expect(
      screen.getByRole("menuitem", { name: "Download ZIP" }),
    ).toHaveAttribute("aria-disabled", "true");
  });
  it("reports download and clipboard failures", async () => {
    downloadVersionZip.mockRejectedValueOnce(new Error("Unavailable"));
    navigator.clipboard.writeText.mockRejectedValueOnce(new Error("Denied"));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Download ZIP" }));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringContaining("Could not download"),
        { variant: "error" },
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Copy install command" }),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenLastCalledWith(
        expect.stringContaining("Could not copy"),
        { variant: "error" },
      ),
    );
  });
  it("copies a production command with environment variables, not credentials", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Copy install command" }),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Install command copied", {
        variant: "success",
      }),
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining("names=support-policy&label=production"),
    );
    expect(navigator.clipboard.writeText.mock.calls[0][0]).toContain(
      "$FI_API_KEY",
    );
  });
  it("archives only after confirmation, then returns to the library", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(useArchiveSkill().mutateAsync).not.toHaveBeenCalled();
    fireEvent.click(
      dialog.getByRole("button", { name: "Archive", exact: true }),
    );
    await waitFor(() =>
      expect(screen.getByText("Skills library")).toBeInTheDocument(),
    );
    expect(useArchiveSkill().mutateAsync).toHaveBeenCalledWith("skill-a");
  });
  it("retains the confirmation and page on archive failure", async () => {
    useArchiveSkill().mutateAsync.mockRejectedValueOnce(new Error("Denied"));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Archive",
        exact: true,
      }),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringContaining("Could not archive"),
        { variant: "error" },
      ),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("URL")).toHaveTextContent(
      "/dashboard/skills/skill-a",
    );
  });
  it.each(["loading", "error", "empty"])(
    "exposes the %s review state",
    (state) => {
      show(`?state=${state}`);
      if (state === "loading")
        expect(screen.getByLabelText("Loading skill")).toBeInTheDocument();
      if (state === "error")
        expect(
          screen.getByText("Could not load this skill"),
        ).toBeInTheDocument();
      if (state === "empty")
        expect(screen.getByText("Not published yet")).toBeInTheDocument();
    },
  );
  it("clears the error preview when retrying without losing the tab", () => {
    show("?state=error&tab=use");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByText("Install commands")).toBeInTheDocument();
    expect(screen.getByLabelText("URL")).toHaveTextContent("?tab=use");
    expect(useSkill().refetch).toHaveBeenCalledTimes(1);
  });
  it("distinguishes not found from transport failure", () => {
    useSkill.mockReturnValue({
      isError: true,
      error: { code: "skill_not_found" },
    });
    const view = show();
    expect(screen.getByText("Skill not found")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Back to Skills" }),
    ).toHaveAttribute("href", "/dashboard/skills");
    view.unmount();
    useSkill.mockReturnValue({
      isError: true,
      error: { code: "network_error" },
      refetch: vi.fn(),
    });
    show();
    expect(screen.getByText("Could not load this skill")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
