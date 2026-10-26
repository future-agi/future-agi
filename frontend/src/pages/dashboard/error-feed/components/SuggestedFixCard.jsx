import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  The analyzer's suggested fix for the issue — what to change, and what would
  prove that change wrong. One per issue; the header's Fix starts from it, and
  the Root cause tab leads its recommendations with it.
*/
/* Where the fix stands, said once on the card; the header's Fix button opens it. */
const stateOf = (status) => {
  const k = status?.key;
  if (!k) return { title: "Suggested fix", note: null };
  if (k === "watching" || k === "resolved")
    return { title: "Shipped fix", note: status.label };
  if (k === "reopened")
    return { title: "Suggested fix", note: "the last fix didn't hold" };
  return {
    title: "Suggested fix",
    note: `a fix built from it is ${String(status.label || "in progress").toLowerCase()}`,
  };
};

export default function SuggestedFixCard({ fix, status }) {
  if (!fix?.change) return null;
  const row = (label, text) => (
    <>
      <Typography
        fontSize="12px"
        fontWeight={500}
        color="text.tertiary"
        sx={{ lineHeight: "20px", whiteSpace: "nowrap" }}
      >
        {label}
      </Typography>
      <Typography
        fontSize="13px"
        color="text.primary"
        sx={{ lineHeight: "20px" }}
      >
        {text}
      </Typography>
    </>
  );
  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        px: 2.25,
        py: 1.5,
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ mb: 1 }}>
        <Iconify
          icon="solar:magic-stick-3-linear"
          width={12}
          sx={{ color: "status.discovery.fg" }}
        />
        <Typography
          fontSize="12px"
          fontWeight={600}
          color="status.discovery.fg"
        >
          {stateOf(status).title}
        </Typography>
        {stateOf(status).note && (
          <Typography fontSize="12px" color="text.tertiary" noWrap>
            {`· ${stateOf(status).note}`}
          </Typography>
        )}
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "auto minmax(0, 1fr)",
          columnGap: 2,
          rowGap: 0.5,
        }}
      >
        {row("Change", fix.change)}
        {fix.wrongIf && row("Wrong if", fix.wrongIf)}
      </Box>
    </Box>
  );
}

SuggestedFixCard.propTypes = {
  status: PropTypes.shape({ key: PropTypes.string, label: PropTypes.string }),
  fix: PropTypes.shape({ change: PropTypes.string, wrongIf: PropTypes.string }),
};
