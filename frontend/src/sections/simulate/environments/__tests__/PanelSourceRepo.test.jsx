import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import PanelSourceRepo from "../panels/PanelSourceRepo";
import { INSTALLATION_ID_LABEL } from "../repoProviders";

const renderPanel = (props) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PanelSourceRepo {...props} />
    </QueryClientProvider>,
  );
};

describe("PanelSourceRepo", () => {
  it("renders three provider chips, two coming soon", () => {
    renderPanel({ onBuild: vi.fn() });
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByText("GitLab")).toBeInTheDocument();
    expect(screen.getByText("Bitbucket")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(2);
  });

  it("keeps GitHub selected when a coming-soon provider is clicked", () => {
    const onBuild = vi.fn();
    renderPanel({ onBuild });
    fireEvent.click(screen.getByText("GitLab"));

    fireEvent.change(screen.getByPlaceholderText("owner/repo"), {
      target: { value: "owner/repo" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onBuild).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "github" }),
    );
  });

  it("gates the CTA until a repository is entered", () => {
    renderPanel({ onBuild: vi.fn() });
    expect(screen.getByText("Add a repository")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Build environment/ })).toBeDisabled();
  });

  it("defaults to Public and hides the installation-id field", () => {
    renderPanel({ onBuild: vi.fn() });
    expect(screen.queryByText(INSTALLATION_ID_LABEL)).toBeNull();
  });

  it("reveals the installation-id field when Private is chosen", () => {
    renderPanel({ onBuild: vi.fn() });
    fireEvent.click(screen.getByText("Private"));
    expect(screen.getByText(INSTALLATION_ID_LABEL)).toBeInTheDocument();
  });

  it("hands the draft to onBuild (public, no installation id)", () => {
    const onBuild = vi.fn();
    renderPanel({ onBuild });
    fireEvent.change(screen.getByPlaceholderText("owner/repo"), {
      target: { value: "owner/repo" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onBuild).toHaveBeenCalledWith({
      kind: "repo",
      provider: "github",
      value: "owner/repo",
      ref: "main",
      entry: "",
      visibility: "public",
      installationId: null,
      envText: null,
      egress: null,
      secretFiles: [],
    });
  });

  it("carries the visibility and installation id when Private", () => {
    const onBuild = vi.fn();
    renderPanel({ onBuild });
    fireEvent.change(screen.getByPlaceholderText("owner/repo"), {
      target: { value: "owner/repo" },
    });
    fireEvent.click(screen.getByText("Private"));
    fireEvent.change(screen.getByPlaceholderText("12345678"), {
      target: { value: "42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onBuild).toHaveBeenCalledWith(
      expect.objectContaining({ visibility: "private", installationId: "42" }),
    );
  });
});
