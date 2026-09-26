import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import OptionCard from "../cards/OptionCard";
import TemplateHeroCard from "../cards/TemplateHeroCard";
import WebEnvironmentsHeroCard from "../cards/WebEnvironmentsHeroCard";
import { OPTIONS, OPTION_ID } from "../environmentOptions";

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
  it("renders the coming-soon prebuilt hero and is inert", () => {
    const onClick = vi.fn();
    render(<TemplateHeroCard onClick={onClick} />);

    expect(screen.getByText("Prebuilt Environments")).toBeInTheDocument();
    expect(screen.getByText("· Fastest")).toBeInTheDocument();
    expect(screen.getByText("Customer Support Line")).toBeInTheDocument();
    expect(screen.getByText("Coding")).toBeInTheDocument();
    expect(screen.getByText("Browser")).toBeInTheDocument();
    expect(screen.getByText("Airline Rebooking")).toBeInTheDocument();
    expect(screen.getByText("+ 10 more")).toBeInTheDocument();
    expect(screen.getByLabelText("Coming soon")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("does not activate on Enter while coming soon", () => {
    const onClick = vi.fn();
    render(<TemplateHeroCard onClick={onClick} />);

    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onClick).not.toHaveBeenCalled();
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
