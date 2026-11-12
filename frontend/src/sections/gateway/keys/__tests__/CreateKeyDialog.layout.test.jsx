import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { select } from "src/theme/overrides/components/select";

import CreateKeyDialog from "../CreateKeyDialog";
import AccessListField from "../AccessListField";
import AllowedProvidersField from "../AllowedProvidersField";

vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return { ...original, default: { post: vi.fn(), get: vi.fn() } };
});
vi.mock("../useKeyAccessOptions", () => ({
  useKeyAccessOptions: () => ({ modelOptions: [] }),
}));
vi.mock("../hooks/useKeyProviderOptions", () => ({
  useKeyProviderOptions: () => ({ options: ["openai"], labelFor: (id) => id }),
}));

// The app's shared override (theme/overrides/components/select.js) paints a
// focused, raised label with the paper colour and 5 px of side padding, a
// mask for a label inside the field's outline. Render with it, in dark mode,
// where the paper colour shows.
const base = createTheme({ palette: palette("dark"), typography });
const darkTheme = createTheme({
  palette: palette("dark"),
  typography,
  components: select(base),
});

function renderIn(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={darkTheme}>
        <MemoryRouter>{ui}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function expectNoBand(input, labelText) {
  act(() => input.focus());
  const label = screen.getByText(labelText, { selector: "label" });
  await waitFor(() => expect(label).toHaveClass("Mui-focused"));
  const style = window.getComputedStyle(label);
  expect(style.backgroundColor).toMatch(/^(transparent|rgba\(0, 0, 0, 0\))$/);
  expect(style.paddingLeft).toMatch(/^0(px)?$/);
}

// In the Create API key dialog the focused label sat on a
// full-width strip of the paper colour, a black bar in dark mode, pushed 5 px
// right of the other labels. These labels sit above their fields.
describe("Create API key dialog labels (layout)", () => {
  it("draws no band behind the focused Name label", async () => {
    renderIn(<CreateKeyDialog open onClose={() => {}} gatewayId="gw-1" />);
    const name = screen.getByRole("textbox", { name: /Name/ });
    await expectNoBand(name, /^Name/);
  });

  it("draws no band behind the focused Allowed models label", async () => {
    renderIn(
      <AccessListField
        label="Allowed models"
        value={[]}
        onChange={() => {}}
        options={[]}
        emptyMeaning="Leave empty to allow every model."
      />,
    );
    const input = screen.getByRole("combobox", { name: "Allowed models" });
    await expectNoBand(input, "Allowed models");
  });

  it("draws no band behind the focused Allowed providers label", async () => {
    renderIn(<AllowedProvidersField value={[]} onChange={() => {}} />);
    const input = screen.getByRole("combobox", { name: "Allowed providers" });
    await expectNoBand(input, "Allowed providers");
  });
});
