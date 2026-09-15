import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { render as baseRender, screen, fireEvent, within } from "src/utils/test-utils";
import PanelCodeUpload from "../panels/PanelCodeUpload";

const render = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return baseRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

const chooseFiles = (container, files) => {
  const input = container.querySelector('input[type="file"][multiple]');
  fireEvent.change(input, { target: { files } });
  return input;
};

describe("PanelCodeUpload", () => {
  it("shows the folder-upload copy and never mentions zip", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    expect(screen.getByText("Upload your agent folder")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/zip/i);
  });

  it("is a folder picker (webkitdirectory) like the product, with no type filter", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    const input = container.querySelector('input[type="file"][multiple]');
    expect(input.hasAttribute("webkitdirectory")).toBe(true);
    expect(input.hasAttribute("directory")).toBe(true);
    expect(input.hasAttribute("accept")).toBe(false);
  });

  it("still accepts a single file through the change handler", () => {
    const onBuild = vi.fn();
    const { container } = render(<PanelCodeUpload onBuild={onBuild} />);
    chooseFiles(container, [new File(["x"], "solo.py")]);
    expect(screen.getByText("solo.py")).toBeInTheDocument();
    expect(screen.getByText("Files (1)")).toBeInTheDocument();
    expect(screen.getByText("1 file")).toBeInTheDocument();
  });

  it("filters excluded files client-side and summarizes the exclusions", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [
      new File(["SECRET=1"], ".env"),
      new File(["a".repeat(1536)], "agent.py"),
    ]);

    expect(screen.getByText("agent.py")).toBeInTheDocument();
    expect(screen.queryByText(".env")).toBeNull();
    expect(screen.getByText("Files (1)")).toBeInTheDocument();
    expect(screen.getByText("1 file · 1 excluded")).toBeInTheDocument();
  });

  it("hands the CTA a draft of only the surviving files", () => {
    const onBuild = vi.fn();
    const { container } = render(<PanelCodeUpload onBuild={onBuild} />);
    chooseFiles(container, [
      new File(["SECRET=1"], ".env"),
      new File(["a".repeat(1536)], "agent.py"),
    ]);
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onBuild).toHaveBeenCalledWith({
      kind: "upload",
      entry: "agent.py",
      files: [{ name: "agent.py", size: 1536 }],
      envText: null,
      egress: null,
      secretFiles: [],
    });
  });

  it("surfaces the thrown message and adds nothing when nothing survives the filter", () => {
    const onBuild = vi.fn();
    const { container } = render(<PanelCodeUpload onBuild={onBuild} />);
    chooseFiles(container, [new File(["SECRET=1"], ".env")]);

    expect(
      screen.getByText("The selected folder contains no uploadable source files."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Files (0)")).toBeNull();
    expect(screen.getByRole("button", { name: /Build environment/ })).toBeDisabled();
  });

  it("surfaces the over-cap message without adding files", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(
      container,
      Array.from({ length: 5001 }, (_, i) => new File(["x"], `f${i}.py`)),
    );

    expect(
      screen.getByText("Source uploads support at most 5000 files and 200 MiB."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Build environment/ })).toBeDisabled();
  });

  it("lists chosen files with sizes and auto-fills the entry field", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [
      new File(["a".repeat(1536)], "agent.py"),
      new File(["x"], "tools.py"),
    ]);

    expect(screen.getByText("agent.py")).toBeInTheDocument();
    expect(screen.getByText("tools.py")).toBeInTheDocument();
    expect(screen.getByText("1.5 KB")).toBeInTheDocument();
    expect(screen.getByText("Files (2)")).toBeInTheDocument();
    expect(screen.getByDisplayValue("agent.py")).toBeInTheDocument();
  });

  it("removes a file via its trash button", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [
      new File(["a".repeat(1536)], "agent.py"),
      new File(["x"], "tools.py"),
    ]);

    const row = screen.getByText("tools.py").parentElement;
    fireEvent.click(within(row).getByRole("button"));
    expect(screen.queryByText("tools.py")).toBeNull();
    expect(screen.getByText("agent.py")).toBeInTheDocument();
  });

  it("gates the CTA until a file is added, then hands the draft to onBuild", () => {
    const onBuild = vi.fn();
    const { container } = render(<PanelCodeUpload onBuild={onBuild} />);
    expect(screen.getByText("Choose a folder to continue")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Build environment/ })).toBeDisabled();

    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onBuild).toHaveBeenCalledWith({
      kind: "upload",
      entry: "agent.py",
      files: [{ name: "agent.py", size: 1536 }],
      envText: null,
      egress: null,
      secretFiles: [],
    });
  });
});
