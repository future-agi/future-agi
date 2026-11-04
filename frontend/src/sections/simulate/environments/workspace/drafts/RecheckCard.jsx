import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { REVIEW_COPY as COPY } from "../workspace.constants";

const NAMES_SHOWN = 20;

/** The scenarios a save rechecks, because the changes alter what's graded. */
export default function RecheckCard({ names }) {
  const [showNames, setShowNames] = useState(false);
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Iconify
          icon="solar:refresh-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s1", flex: 1 }}>
          {COPY.rechecked(names.length)}
        </Typography>
        <Button
          size="small"
          color="inherit"
          aria-expanded={showNames}
          onClick={() => setShowNames((shown) => !shown)}
          sx={{ flexShrink: 0, color: "text.secondary" }}
        >
          {showNames ? COPY.hide : COPY.showWhich}
        </Button>
      </Stack>
      <Collapse in={showNames} unmountOnExit>
        <Stack component="ul" sx={{ m: 0, mt: 0.5, pl: 3.5 }}>
          {names.slice(0, NAMES_SHOWN).map((name) => (
            <Typography
              key={name}
              component="li"
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {name}
            </Typography>
          ))}
          {names.length > NAMES_SHOWN && (
            <Typography
              component="li"
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {COPY.andMore(names.length - NAMES_SHOWN)}
            </Typography>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}

RecheckCard.propTypes = {
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
};
