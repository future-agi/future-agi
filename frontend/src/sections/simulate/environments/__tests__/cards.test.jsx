import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

const prebuilt = { data: [] };
vi.mock("src/api/simulate-environments/prebuilt", () => ({
  usePrebuiltEnvironments: () => prebuilt,
}));

const { default: OptionCard } = await import("../cards/OptionCard");
const { default: TemplateHeroCard } = await import("../cards/TemplateHeroCard");
const { default: WebEnvironmentsHeroCard } = await import(
  "../cards/WebEnvironmentsHeroCard"
);
const { OPTIONS, OPTION_ID } = await import("../environmentOptions");

const optionById = (id) => OPTIONS.find((o) => o.id === id);

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe("OptionCard", () => {
  it("live option: clickable, unlabelled, renders title, blurb and preview chips", () => {
    const onClick = vi.fn();
    const option = optionById(OPTION_ID.SOURCE);
    render(<OptionCard option={option} onClick={onClick} />);

    expect(screen.queryByLabelText("Coming soon")).toBeNull();
    expect(screen.getByText(option.title)).toBeInTheDocument();
    expect(screen.getByText(option.blurb)).toBeInTheDocument();
    option.preview.forEach((chip) => {
      expect(screen.getByText(chip)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("live option: keyboard-focusable and fires on Enter", () => {
    const onClick = vi.fn();
    render(<OptionCard option={optionById(OPTION_ID.SOURCE)} onClick={onClick} />);

    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("coming-soon option: labelled and inert", () => {
    const onClick = vi.fn();
    render(<OptionCard option={optionById(OPTION_ID.MCP)} onClick={onClick} />);

    expect(screen.getByLabelText("Coming soon")).toBeInTheDocument();
    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("tabindex", "-1");
    fireEvent.click(btn);
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onClick).not.toHaveBeenCalled();
  });

  it("selected option: exposes aria-pressed", () => {
    render(<OptionCard option={optionById(OPTION_ID.SOURCE)} selected onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });
});

describe("TemplateHeroCard", () => {
  const named = (...names) => names.map((name) => ({ id: name, name }));

  it("names the library's own templates and opens it on click", () => {
    prebuilt.data = named("Collections", "Insurance FNOL", "Healthcare", "Banking");
    const onClick = vi.fn();
    render(<TemplateHeroCard onClick={onClick} />);

    expect(screen.getByText("Prebuilt Environments")).toBeInTheDocument();
    ["Collections", "Insurance FNOL", "Healthcare", "Banking"].forEach((name) => {
      expect(screen.getByText(name)).toBeInTheDocument();
    });
    expect(screen.queryByText(/more$/)).toBeNull();
    expect(screen.queryByLabelText("Coming soon")).toBeNull();

    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("counts the templates it does not name", () => {
    prebuilt.data = named("A", "B", "C", "D", "E", "F");
    render(<TemplateHeroCard onClick={vi.fn()} />);

    expect(screen.queryByText("E")).toBeNull();
    expect(screen.getByText("+ 2 more")).toBeInTheDocument();
  });
});

describe("WebEnvironmentsHeroCard", () => {
  it("renders the coming-soon web hero and is inert", () => {
    const onClick = vi.fn();
    render(<WebEnvironmentsHeroCard onClick={onClick} />);

    expect(screen.getByText("Web Environments")).toBeInTheDocument();
    ["Slack", "Notion", "Gmail", "Salesforce", "Linear"].forEach((chip) => {
      expect(screen.getByText(chip)).toBeInTheDocument();
    });
    expect(screen.getByText("+ 8 more")).toBeInTheDocument();
    expect(screen.getByLabelText("Coming soon")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });
});
