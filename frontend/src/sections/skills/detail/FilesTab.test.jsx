import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";
import { useSkillFile, useSkillVersion } from "src/api/skills";
import { enqueueSnackbar } from "notistack";
import FilesTab from "./FilesTab";

vi.mock("src/api/skills", () => ({
  useSkillFile: vi.fn(),
  useSkillVersion: vi.fn(),
  getSkillErrorCode: (error) => error?.code,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/form-code-editor", () => ({
  FormCodeEditor: vi.fn(({ readOnly, language, control }) => (
    <textarea
      aria-label={`Raw ${language}`}
      readOnly={readOnly}
      value={control._formValues.content}
    />
  )),
}));
vi.mock("../components/VersionSelect", () => ({
  default: vi.fn(({ value, onChange }) => (
    <select
      aria-label="Version"
      value={value || ""}
      onChange={(event) => onChange(Number(event.target.value))}
    >
      <option value="1">v1</option>
      <option value="2">v2</option>
      <option value="label:production">production</option>
      <option value="label:missing">missing</option>
    </select>
  )),
}));
vi.mock("../components/FileTree", () => ({
  default: vi.fn(({ files, onSelect }) => (
    <nav aria-label="Files">
      {files.map((file) => (
        <button
          type="button"
          key={file.path}
          onClick={() => onSelect(file.path)}
        >
          {file.path}
        </button>
      ))}
    </nav>
  )),
}));
vi.mock("../components/SkillMarkdownPreview", () => ({
  default: vi.fn(({ content, onOpenPath }) => (
    <div>
      {content}
      <button type="button" onClick={() => onOpenPath("references/policy.md")}>
        Open policy
      </button>
    </div>
  )),
}));

const skill = {
  id: "skill-a",
  latestVersion: 2,
  labels: [{ name: "production", version: 1 }],
};
const files = [
  { path: "SKILL.md", sizeBytes: 100, tokenEstimate: 25 },
  { path: "references/policy.md", sizeBytes: 80, tokenEstimate: 20 },
  { path: "scripts/check.py", sizeBytes: 50, tokenEstimate: 13 },
];
const success = (data) => ({
  data,
  isPending: false,
  isError: false,
  refetch: vi.fn(),
});
const theme = createTheme({
  palette: dsPalette("light"),
  typography: dsTypography,
});
function Location() {
  const location = useLocation();
  return <output aria-label="URL">{location.search}</output>;
}
function show(search = "", overrides = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[`/dashboard/skills/skill-a${search}`]}>
        <FilesTab skill={{ ...skill, ...overrides }} />
        <Location />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useSkillVersion.mockImplementation((_id, number) =>
    success({
      number,
      files,
      checks: {
        items: [
          {
            code: "executable_content",
            severity: "info",
            message: "Review scripts before promoting.",
            path: "scripts/check.py",
          },
        ],
      },
    }),
  );
  useSkillFile.mockImplementation((_id, _number, path) =>
    success({ path, text: `Contents of ${path}` }),
  );
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});
afterEach(cleanup);

describe("Files tab", () => {
  it("opens the pinned version and file from the URL", () => {
    show("?version=1&path=references%2Fpolicy.md&tab=files");
    expect(useSkillVersion).toHaveBeenCalledWith("skill-a", 1);
    expect(useSkillFile).toHaveBeenCalledWith(
      "skill-a",
      1,
      "references/policy.md",
    );
    expect(
      screen.getByText("Contents of references/policy.md"),
    ).toBeInTheDocument();
    expect(screen.getByText("80 B · ≈ 20 tokens")).toBeInTheDocument();
  });
  it("opens sibling links without losing the version or unrelated query state", () => {
    show("?version=1&tab=files&source=shared");
    fireEvent.click(screen.getByRole("button", { name: "Open policy" }));
    expect(screen.getByLabelText("URL")).toHaveTextContent(
      "path=references%2Fpolicy.md",
    );
    expect(screen.getByLabelText("URL")).toHaveTextContent("source=shared");
    expect(useSkillFile).toHaveBeenLastCalledWith(
      "skill-a",
      1,
      "references/policy.md",
    );
  });
  it("resets the path when switching versions", () => {
    show("?version=1&path=references%2Fpolicy.md&tab=files");
    fireEvent.change(screen.getByLabelText("Version"), {
      target: { value: "2" },
    });
    expect(screen.getByLabelText("URL")).toHaveTextContent(
      "version=2&path=SKILL.md&tab=files",
    );
  });
  it("renders raw Markdown and non-Markdown with a read-only code editor", () => {
    show();
    fireEvent.click(screen.getByRole("tab", { name: "Raw" }));
    expect(
      screen.getByRole("textbox", { name: "Raw markdown" }),
    ).toHaveAttribute("readonly");
    fireEvent.click(screen.getByRole("button", { name: "scripts/check.py" }));
    expect(screen.getByRole("textbox", { name: "Raw python" })).toHaveValue(
      "Contents of scripts/check.py",
    );
    expect(
      screen.queryByRole("tab", { name: "Preview" }),
    ).not.toBeInTheDocument();
  });
  it("copies the complete fetched text and reports clipboard failure", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Copy file" }));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("File copied", {
        variant: "success",
      }),
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "Contents of SKILL.md",
    );
    navigator.clipboard.writeText.mockRejectedValueOnce(
      new Error("Unavailable"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy file" }));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenLastCalledWith(
        expect.stringContaining("Couldn't copy"),
        { variant: "error" },
      ),
    );
  });
  it("shows checks for the viewed version and opens their file", () => {
    show("?version=1");
    expect(screen.getByText("Checks for v1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show checks" }));
    expect(screen.getByRole("button", { name: "Hide checks" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Review scripts before promoting/ }),
    );
    expect(useSkillFile).toHaveBeenLastCalledWith(
      "skill-a",
      1,
      "scripts/check.py",
    );
  });
  it("does not fetch a missing path or fall back from a missing label", () => {
    const view = show("?path=missing.md");
    expect(screen.getByText("File not found")).toBeInTheDocument();
    expect(useSkillFile).toHaveBeenLastCalledWith("skill-a", 2, null);
    view.unmount();
    show("?version=label:missing");
    expect(screen.getByText("Version not found")).toBeInTheDocument();
    expect(useSkillVersion).toHaveBeenLastCalledWith("skill-a", null);
  });
  it("renders unpublished and loading states", () => {
    const view = show("", { latestVersion: null });
    expect(screen.getByText("No published files yet")).toBeInTheDocument();
    view.unmount();
    useSkillVersion.mockReturnValue({ isPending: true });
    show();
    expect(screen.getByLabelText("Loading version")).toBeInTheDocument();
  });
  it("retries a version error", () => {
    const refetch = vi.fn();
    useSkillVersion.mockReturnValue({
      isError: true,
      error: { code: "version_not_found" },
      refetch,
    });
    show();
    expect(screen.getByText("Version not found")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
  it("renders file loading and retryable file errors", () => {
    useSkillFile.mockReturnValue({ isPending: true });
    const view = show();
    expect(screen.getByLabelText("Loading file")).toBeInTheDocument();
    view.unmount();
    const refetch = vi.fn();
    useSkillFile.mockReturnValue({ isError: true, refetch });
    show();
    expect(screen.getByText("Couldn't load this file")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
