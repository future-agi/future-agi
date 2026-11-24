import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import { ACTORS_COPY } from "./actors.constants";

const R = ACTORS_COPY.relations;

export function actorEdges(cast) {
  const names = Object.fromEntries(
    cast.map((one) => [one.id, one.name || one.id]),
  );
  const edges = [];
  const seen = new Set();
  const add = (from, to, label) => {
    const key = `${from}→${to}`;
    if (seen.has(key)) return;
    seen.add(key);
    edges.push({ key, from, to, label });
  };
  cast.forEach((actor) => {
    const name = names[actor.id];
    const when = actor.when_label || "";
    if (actor.side === "caller") add(name, R.agent, when);
    else if (actor.parent) add(names[actor.parent] || actor.parent, name, when);
    else add(R.agent, name, when);
  });
  cast.forEach((actor) =>
    (actor.reaches || []).forEach((id) =>
      add(names[actor.id], names[id] || id, R.canReach),
    ),
  );
  return edges;
}

const cellSx = { px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" };

export default function ActorRelations({ cast }) {
  const edges = actorEdges(cast);
  if (!edges.length) return null;
  return (
    <SectionCard title={R.title} subtitle={R.subtitle}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "minmax(200px, 280px) minmax(0, 1fr)",
        }}
      >
        {edges.map((edge) => (
          <Box key={edge.key} sx={{ display: "contents" }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={cellSx}
            >
              <Typography sx={{ typography: "s2" }}>{edge.from}</Typography>
              <Iconify
                icon="solar:arrow-right-linear"
                width={12}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography sx={{ typography: "s2" }}>{edge.to}</Typography>
            </Stack>
            <Typography
              sx={{
                ...cellSx,
                typography: "s2",
                color: "text.secondary",
                wordBreak: "break-word",
              }}
            >
              {edge.label}
            </Typography>
          </Box>
        ))}
      </Box>
    </SectionCard>
  );
}

ActorRelations.propTypes = {
  cast: PropTypes.arrayOf(PropTypes.object).isRequired,
};
