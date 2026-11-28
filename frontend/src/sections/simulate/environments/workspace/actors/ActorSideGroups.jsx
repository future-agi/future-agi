import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;

function SideHeader({ title, count }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        py: 0.75,
        bgcolor: "background.neutral",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        {title}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {count}
      </Typography>
    </Stack>
  );
}

SideHeader.propTypes = {
  title: PropTypes.string.isRequired,
  count: PropTypes.number.isRequired,
};

// Caller's side first; within a side the cast's own order is kept.
export default function ActorSideGroups({ cast, renderRows }) {
  return Object.entries(C.sides).map(([side, { label }]) => {
    const group = cast.filter((one) => one.side === side);
    return (
      <Box key={side} data-testid={`actors-side-${side}`}>
        <SideHeader title={label} count={group.length} />
        {group.length ? (
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {renderRows(group)}
          </Stack>
        ) : (
          <Typography
            sx={{
              px: 1.5,
              py: 1.25,
              typography: "s2",
              color: "text.secondary",
            }}
          >
            {C.emptySide}
          </Typography>
        )}
      </Box>
    );
  });
}

ActorSideGroups.propTypes = {
  cast: PropTypes.arrayOf(PropTypes.object).isRequired,
  // (actors) => rows
  renderRows: PropTypes.func.isRequired,
};
