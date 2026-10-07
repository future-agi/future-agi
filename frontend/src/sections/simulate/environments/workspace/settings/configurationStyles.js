export const MONO = "ui-monospace, Menlo, monospace";

export const MASK = "••••••••••••";

export const valueSx = {
  flex: 1,
  minWidth: 0,
  typography: "s2",
  fontFamily: MONO,
  color: "text.subtitle",
};

export const monoInputSx = {
  "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO },
};

export const compactInputSx = {
  "& .MuiInputBase-root": { fontFamily: MONO },
  "& .MuiInputBase-input": {
    typography: "s2",
    fontFamily: MONO,
    height: 18,
    py: "7px",
    px: 1.25,
  },
};

export const inlineInputSx = {
  "& .MuiInputBase-root": { fontFamily: MONO },
  "& .MuiInputBase-input": {
    typography: "s2",
    fontFamily: MONO,
    height: 18,
    py: "5px",
    px: 1,
  },
};

export const outlinedButtonSx = {
  color: "text.primary",
  borderColor: "divider",
  typography: "s2",
  fontWeight: 600,
};

export const dividerSx = { borderBottom: "1px solid", borderColor: "divider" };
