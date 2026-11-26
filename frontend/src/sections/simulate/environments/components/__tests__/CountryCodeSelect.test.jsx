import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import CountryCodeSelect from "../CountryCodeSelect";

const optionTexts = () => screen.getAllByRole("option").map((o) => o.textContent);

describe("CountryCodeSelect", () => {
  it("shows the chosen dial code collapsed", () => {
    render(<CountryCodeSelect value="US" onChange={() => {}} />);
    expect(screen.getByRole("combobox")).toHaveValue("+1");
  });

  it("filters by country name as the user types", async () => {
    const user = userEvent.setup();
    render(<CountryCodeSelect value="US" onChange={() => {}} />);
    await user.click(screen.getByRole("combobox"));
    await user.clear(screen.getByRole("combobox"));
    await user.type(screen.getByRole("combobox"), "indi");
    const names = optionTexts();
    expect(names.some((n) => n.includes("India"))).toBe(true);
    expect(names.every((n) => /indi/i.test(n))).toBe(true);
  });

  it("filters by dial code and reports the picked country's ISO code", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CountryCodeSelect value="US" onChange={onChange} />);
    await user.click(screen.getByRole("combobox"));
    await user.clear(screen.getByRole("combobox"));
    await user.type(screen.getByRole("combobox"), "+44");
    await user.click(screen.getAllByRole("option").find((o) => o.textContent.includes("United Kingdom")));
    expect(onChange).toHaveBeenCalledWith("GB");
  });

  it("filters by ISO code, so Canada and the US are told apart despite sharing +1", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CountryCodeSelect value="US" onChange={onChange} />);
    await user.click(screen.getByRole("combobox"));
    await user.clear(screen.getByRole("combobox"));
    await user.type(screen.getByRole("combobox"), "CA");
    await user.click(screen.getAllByRole("option").find((o) => o.textContent.includes("Canada")));
    expect(onChange).toHaveBeenCalledWith("CA");
  });
});
