import PropTypes from "prop-types";
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
} from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { ACTORS_COPY } from "./actors.constants";
import { actorTitle } from "./actorUtils";

const R = ACTORS_COPY.relations;
const CELL = {
  typography: "s2",
  verticalAlign: "top",
  wordBreak: "break-word",
};

export default function ActorRelations({ cast }) {
  if (!cast.length) return null;
  // Who brings in or is reached by whom reads by person: a role can be shared by several.
  const names = Object.fromEntries(
    cast.map((one) => [one.id, one.name || one.id]),
  );
  return (
    <SectionCard title={R.title} subtitle={R.subtitle}>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              {Object.values(R.columns).map((label) => (
                <TableCell
                  key={label}
                  sx={{
                    ...CELL,
                    fontWeight: 600,
                    color: "text.secondary",
                    whiteSpace: "nowrap",
                  }}
                >
                  {label}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {cast.map((actor) => {
              const reaches = actor.reaches.map((id) => names[id] || id);
              return (
                <TableRow key={actor.id}>
                  <TableCell sx={{ ...CELL, fontWeight: 600 }}>
                    {[actorTitle(actor).main, actorTitle(actor).speaksAs]
                      .filter(Boolean)
                      .join(" · ")}
                  </TableCell>
                  <TableCell sx={CELL}>{actor.when_label}</TableCell>
                  <TableCell sx={CELL}>
                    {actor.parent
                      ? names[actor.parent] || actor.parent
                      : R.none}
                  </TableCell>
                  <TableCell sx={CELL}>
                    {reaches.length ? reaches.join(", ") : R.reachesNoOne}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
    </SectionCard>
  );
}

ActorRelations.propTypes = {
  cast: PropTypes.arrayOf(PropTypes.object).isRequired,
};
