// Tinted pill colours that read in light and dark mode (alpha 0.12-ish
// backgrounds from the palette's `o10`, text from the scale: 700 in light,
// 300 in dark), the way the shared primitives pick `.dark` / `.light`.
const scale = { pass: "green", warn: "orange", fail: "red", accent: "purple" };

export function toneSx(tone) {
  const name = scale[tone];
  if (!name) return { bgcolor: "background.neutral", color: "text.secondary" };
  return {
    bgcolor: `${name}.o10`,
    color: (theme) =>
      theme.palette.mode === "dark"
        ? theme.palette[name]?.[300] ?? theme.palette.text.primary
        : theme.palette[name]?.[700] ?? theme.palette.text.primary,
  };
}
