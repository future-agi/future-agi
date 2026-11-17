import { dsPalette } from "../ds-tokens";

// Bold contrast keeps stronger panel boundaries, not decorative elevation.
export function createContrast(contrast, mode) {
  const p = dsPalette(mode);
  return {
    components:
      contrast === "bold"
        ? {
            MuiCard: {
              styleOverrides: {
                root: { boxShadow: "none", borderColor: p.border.strong },
              },
            },
          }
        : {},
  };
}
