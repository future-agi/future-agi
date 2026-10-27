import Box from "@mui/material/Box";

export default function RequiredMark() {
  return (
    <Box
      component="span"
      sx={{
        color: (t) => (t.palette.mode === "dark" ? "error.light" : "#d32f2f"),
      }}
    >
      *
    </Box>
  );
}
