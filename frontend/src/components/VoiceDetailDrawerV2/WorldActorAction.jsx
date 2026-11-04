import PropTypes from "prop-types";
import { useId, useState } from "react";
import { Box, ButtonBase, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { WORLD_ACTION_DETAILS } from "./constants";

// A world actor's action as an event: what they did and how it went, with its raw record folded away.
export default function WorldActorAction({
  text,
  renderText = (line) => line,
}) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const [event, ...details] = text.split("\n");
  return (
    <Box>
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography variant="s1" sx={{ overflowWrap: "anywhere" }}>
          {renderText(event)}
        </Typography>
        {details.length > 0 && (
          <ButtonBase
            aria-expanded={open}
            aria-controls={open ? detailsId : undefined}
            aria-label={`${WORLD_ACTION_DETAILS}: ${event}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpen((was) => !was);
            }}
            sx={{ typography: "s3", color: "text.secondary", gap: 0.25 }}
          >
            {WORLD_ACTION_DETAILS}
            <Iconify
              icon={
                open
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={12}
            />
          </ButtonBase>
        )}
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box
          id={detailsId}
          sx={{
            mt: 0.5,
            fontFamily: "monospace",
            typography: "s2",
            color: "text.secondary",
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
          }}
        >
          {renderText(details.join("\n"))}
        </Box>
      </Collapse>
    </Box>
  );
}

WorldActorAction.propTypes = {
  text: PropTypes.string.isRequired,
  renderText: PropTypes.func,
};
