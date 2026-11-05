import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  A build stopped in Needs repair (builder.md: "show the failure and affected
  scenarios, then repair or remove the affected requirement with consent").
  The draft is kept; Repair adds what's missing, Remove asks first.
*/
export default function NeedsRepairStrip({
  invalid,
  affected,
  message,
  onRepair,
  onRemove,
}) {
  /* Removing changes the world, so it's asked first. */
  const [asking, setAsking] = useState(false);
  const shown = affected.slice(0, 3);
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={1.25}
      sx={{
        px: 2,
        py: 1.125,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        bgcolor: "background.neutral",
      }}
    >
      <Iconify
        icon="solar:danger-triangle-linear"
        width={16}
        sx={{ color: "status.warning.fg", flexShrink: 0, mt: 0.25 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`Needs repair — ${invalid.check} failed`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {message}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {`Affects ${affected.length} scenario${affected.length === 1 ? "" : "s"}: ${shown.map((a) => a.title.replace(/\.$/, "")).join("; ")}${affected.length > shown.length ? ` and ${affected.length - shown.length} more` : ""}.`}
        </Typography>
        {asking && (
          <Typography sx={{ typography: "s3", mt: 0.5 }}>
            {`Remove ${invalid.table} from the world? Its ${affected.length} scenario${affected.length === 1 ? "" : "s"} won't be written.`}
          </Typography>
        )}
      </Box>
      {asking ? (
        <>
          <Button
            size="small"
            variant="outlined"
            onClick={onRemove}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            {`Remove ${invalid.table}`}
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setAsking(false)}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            Keep it
          </Button>
        </>
      ) : (
        <>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onRepair}
            sx={{ flexShrink: 0 }}
          >
            {`Repair · add ${invalid.missing}`}
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setAsking(true)}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            Remove that requirement
          </Button>
        </>
      )}
    </Stack>
  );
}
NeedsRepairStrip.propTypes = {
  invalid: PropTypes.object.isRequired,
  affected: PropTypes.array.isRequired,
  message: PropTypes.string,
  onRepair: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};
