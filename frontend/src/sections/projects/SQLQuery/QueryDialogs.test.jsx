import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import {
  createTheme,
  getContrastRatio,
  hexToRgb,
  ThemeProvider,
} from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { componentsOverrides } from "src/theme/overrides";
import SaveQueryDialog from "./SaveQueryDialog";
import QueryHistoryDialog from "./QueryHistoryDialog";

const productionTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    shadows: shadows(mode),
    customShadows: customShadows(mode),
  });
  theme.components = componentsOverrides(theme);
  return theme;
};
const saveProps = () => ({
  open: true,
  onClose: vi.fn(),
  onSave: vi.fn(),
  updatingSaved: false,
  name: "Span count",
  onNameChange: vi.fn(),
  visibility: "private",
  onVisibilityChange: vi.fn(),
  saving: false,
  sql: "SELECT count() FROM spans",
  hours: 24,
  chart: { type: "table" },
});

describe.each(["light", "dark"])(
  "SQL dialogs in the production %s theme",
  (mode) => {
    it("keeps the Visibility label readable, the save action neutral, and close named", () => {
      const theme = productionTheme(mode);
      const props = saveProps();
      render(
        <ThemeProvider theme={theme}>
          <SaveQueryDialog {...props} />
        </ThemeProvider>,
      );
      const dialog = screen.getByRole("dialog", { name: /^Save query/ });
      const visibility = within(dialog).getByRole("radiogroup", {
        name: "Visibility",
        exact: true,
      });
      const label = document.getElementById(
        visibility.getAttribute("aria-labelledby"),
      );
      const assertContrast = () => {
        const foreground = getComputedStyle(label).color;
        const background = getComputedStyle(dialog).backgroundColor;
        expect(foreground).toBe(hexToRgb(theme.palette.text.secondary));
        expect(background).toBe(hexToRgb(theme.palette.background.overlay));
        expect(getContrastRatio(foreground, background)).toBeGreaterThanOrEqual(
          4.5,
        );
      };
      assertContrast();
      const project = within(visibility).getByRole("radio", {
        name: /^Project members/,
      });
      fireEvent.focus(project);
      assertContrast();
      fireEvent.click(project);
      expect(props.onVisibilityChange).toHaveBeenCalledWith("project");
      const save = within(dialog).getByRole("button", {
        name: "Save",
        exact: true,
      });
      expect(save).toHaveClass("MuiButton-containedInherit");
      fireEvent.click(save);
      expect(props.onSave).toHaveBeenCalledOnce();
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Close", exact: true }),
      );
      expect(props.onClose).toHaveBeenCalledOnce();
    });

    it("names Recent queries and delegates opening a history item", () => {
      const onSelect = vi.fn();
      const onClose = vi.fn();
      const sql = "SELECT count() FROM spans";
      const props = {
        open: true,
        onSelect,
        onClose,
        history: [
          {
            id: "run",
            sql,
            state: "failed",
            created_at: "2026-10-01T00:00:00Z",
            error: "Query failed",
            expired: true,
          },
        ],
      };
      const { rerender } = render(
        <ThemeProvider theme={productionTheme(mode)}>
          <QueryHistoryDialog {...props} />
        </ThemeProvider>,
      );
      const dialog = screen.getByRole("dialog", {
        name: "Recent queries",
        exact: true,
      });
      expect(
        within(dialog).getByRole("heading", { name: "Recent queries" }),
      ).toBeVisible();
      expect(within(dialog).getByText("Query failed")).toBeVisible();
      expect(within(dialog).getByText(/results expired/)).toBeVisible();
      fireEvent.click(
        within(dialog).getByRole("button", { name: /SELECT count/ }),
      );
      expect(onSelect).toHaveBeenCalledWith(sql);
      rerender(
        <ThemeProvider theme={productionTheme(mode)}>
          <QueryHistoryDialog {...props} history={[]} />
        </ThemeProvider>,
      );
      expect(
        screen.getByRole("dialog", { name: "Recent queries", exact: true }),
      ).toBeVisible();
      expect(screen.getByText("No queries yet")).toBeVisible();
      fireEvent.click(
        screen.getByRole("button", { name: "Close", exact: true }),
      );
      expect(onClose).toHaveBeenCalledOnce();
    });
  },
);

it("retains update wording, form callbacks, and loading/error states after extraction", () => {
  const props = {
    ...saveProps(),
    updatingSaved: true,
    selectedName: "Existing count",
    name: "",
    error: "Could not save",
  };
  const theme = productionTheme("light");
  const { rerender } = render(
    <ThemeProvider theme={theme}>
      <SaveQueryDialog {...props} />
    </ThemeProvider>,
  );
  const dialog = screen.getByRole("dialog", { name: /^Update saved query/ });
  expect(
    within(dialog).getByText(/Saving replaces “Existing count”/),
  ).toBeVisible();
  expect(within(dialog).getByRole("alert")).toHaveTextContent("Could not save");
  expect(
    within(dialog).getByRole("button", { name: "Save changes" }),
  ).toBeDisabled();
  fireEvent.change(
    within(dialog).getByRole("textbox", { name: "Query name" }),
    { target: { value: "New count" } },
  );
  expect(props.onNameChange).toHaveBeenCalledWith("New count");
  rerender(
    <ThemeProvider theme={theme}>
      <SaveQueryDialog {...props} name="New count" saving />
    </ThemeProvider>,
  );
  expect(
    within(dialog).getByRole("button", { name: "Save changes" }),
  ).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
});
