import { describe, it, expect, vi } from "vitest";
import { createTheme, getContrastRatio } from "@mui/material/styles";
import { render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import CreateQueueDrawer from "../create-queue-drawer";

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useCreateAnnotationQueue: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useUpdateAnnotationQueue: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useUpdateAnnotationQueueStatus: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// A stable user object: the drawer's reset effect lists `user` in its deps.
vi.mock("src/auth/hooks", () => {
  const user = { id: "user-1" };
  return { useAuthContext: () => ({ user }) };
});

vi.mock("../components/label-picker", () => ({
  default: () => <div data-testid="label-picker" />,
}));

vi.mock("../components/annotator-picker", () => ({
  default: () => <div data-testid="annotator-picker" />,
}));

// The app's theme, built as theme/index.jsx builds it.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

const rgb = (hex) =>
  `rgb(${hex
    .slice(1)
    .match(/../g)
    .map((v) => parseInt(v, 16))
    .join(", ")})`;

const HELPER_TEXTS = [
  /^A short, descriptive name annotators will recognize$/,
  /^How many annotators must label each item/,
  /^Supports markdown formatting$/,
  /^How long an item stays reserved for an annotator$/,
];

describe("Create queue drawer helper text", () => {
  it.each(["light", "dark"])("reads at 4.5:1 or better in %s", (mode) => {
    const theme = appTheme(mode);
    const { secondary } = theme.palette.text;
    expect(
      getContrastRatio(secondary, theme.palette.background.paper),
    ).toBeGreaterThanOrEqual(4.5);

    render(<CreateQueueDrawer open onClose={vi.fn()} />, { theme });

    // text.disabled took these lines to about 2.6:1.
    HELPER_TEXTS.forEach((text) => {
      expect(getComputedStyle(screen.getByText(text)).color).toBe(
        rgb(secondary),
      );
    });
  });
});
