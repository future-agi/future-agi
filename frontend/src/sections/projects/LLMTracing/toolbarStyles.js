export const pillSx = {
  textTransform: "none",
  fontWeight: 500,
  fontSize: 13,
  fontFamily: "'IBM Plex Sans', sans-serif",
  height: 26,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "4px",
  color: "text.primary",
  bgcolor: "background.paper",
  px: 1,
  "&:hover": { bgcolor: "background.neutral", borderColor: "text.disabled" },
};

export const pillFilledSx = {
  ...pillSx,
  color: "primary.contrastText",
  bgcolor: "primary.main",
  borderColor: "primary.main",
  "&:hover": { bgcolor: "primary.dark", borderColor: "primary.dark" },
};
