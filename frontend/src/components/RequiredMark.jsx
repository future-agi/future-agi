import Box from "@mui/material/Box";

export default function RequiredMark() {
  return (
    <Box
      component="span"
      sx={{
        color: "status.danger.fg",
      }}
    >
      *
    </Box>
  );
}
