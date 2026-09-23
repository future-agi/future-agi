import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import ChipCard from "../components/ChipCard";
import ProviderRow from "../components/ProviderRow";
import ContinueRow from "../components/ContinueRow";
import PlatformLogo from "../components/PlatformLogo";
import Field from "../components/Field";

const PROVIDER_OPTIONS = [
  { id: "github", name: "GitHub", icon: "eva:github-fill" },
  { id: "gitlab", name: "GitLab", icon: "logos:gitlab", comingSoon: true },
  { id: "bitbucket", name: "Bitbucket", icon: "logos:bitbucket", comingSoon: true },
];

describe("ChipCard", () => {
  it("coming soon: labelled and inert", () => {
    const onClick = vi.fn();
    render(<ChipCard label="GitLab" comingSoon onClick={onClick} />);
    // The coming-soon chip sits alongside the label (not a wrapping tooltip).
    expect(screen.getByLabelText("Coming soon")).toBeInTheDocument();
    expect(screen.getByText("GitLab")).toBeInTheDocument();
    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("tabindex", "-1");
    fireEvent.click(screen.getByText("GitLab"));
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onClick).not.toHaveBeenCalled();
  });

  it("live: clickable and unlabelled", () => {
    const onClick = vi.fn();
    render(<ChipCard label="GitHub" onClick={onClick} />);
    expect(screen.queryByLabelText("Coming soon")).toBeNull();
    fireEvent.click(screen.getByText("GitHub"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("exposes a keyboard button with pressed state and fires on Enter", () => {
    const onClick = vi.fn();
    render(<ChipCard label="GitHub" on onClick={onClick} />);
    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("tabindex", "0");
    expect(btn).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("ProviderRow", () => {
  it("gates coming-soon providers and forwards the live pick", () => {
    const onChange = vi.fn();
    render(<ProviderRow options={PROVIDER_OPTIONS} value="github" onChange={onChange} />);
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(2);
    fireEvent.click(screen.getByText("GitLab"));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("GitHub"));
    expect(onChange).toHaveBeenCalledWith("github");
  });
});

describe("ContinueRow", () => {
  it("disabled shows the hint and a disabled button", () => {
    const onClick = vi.fn();
    render(<ContinueRow disabled hint="Add a repository" onClick={onClick} />);
    expect(screen.getByText("Add a repository")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Build environment/ })).toBeDisabled();
  });

  it("enabled hides the hint and fires on click", () => {
    const onClick = vi.fn();
    render(<ContinueRow hint="Add a repository" onClick={onClick} />);
    expect(screen.queryByText("Add a repository")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("PlatformLogo", () => {
  it("renders the inlined svg for a bundled logo", () => {
    render(<PlatformLogo id="vapi" name="Vapi" />);
    const el = screen.getByLabelText("Vapi");
    expect(el.querySelector("svg")).not.toBeNull();
  });

  it("renders a monogram for a platform without a bundled logo", () => {
    render(<PlatformLogo id="openai_assistants" name="OpenAI Assistants" />);
    expect(screen.getByText("O")).toBeInTheDocument();
  });
});

describe("Field", () => {
  it("renders the required marker and reports typed text", () => {
    const onChange = vi.fn();
    render(<Field label="Repository" required value="" onChange={onChange} />);
    expect(screen.getByText("*")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "owner/repo" } });
    expect(onChange).toHaveBeenCalledWith("owner/repo");
  });
});
