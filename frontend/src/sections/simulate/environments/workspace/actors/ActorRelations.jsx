import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { callActorWhen } from "../scenarios/callActorLabels";
import { ACTORS_COPY } from "./actors.constants";

const R = ACTORS_COPY.relations;
const quiet = {
  typography: "s3",
  color: "text.secondary",
  wordBreak: "break-word",
};

// Who an actor's permitted actions reach: another actor whose identity in that service is what the action addresses.
export const contactsOf = (actor, cast) => {
  const services = new Set(
    (actor.permitted_actions || []).map((one) => String(one).split(".")[0]),
  );
  return cast.filter(
    (other) =>
      other.id !== actor.id &&
      Object.keys(other.identities || {}).some((service) =>
        services.has(service),
      ),
  );
};

/**
 * The relationship view: who can contact whom, each actor's role and when it comes in. Read-only, drawn
 * from the records themselves.
 */
export default function ActorRelations({ cast }) {
  if (!cast.length) return null;
  const names = Object.fromEntries(
    cast.map((one) => [one.id, one.name || one.id]),
  );
  return (
    <SectionCard title={R.title} subtitle={R.subtitle}>
      <Stack spacing={1.25}>
        {cast.map((actor) => {
          const reaches = contactsOf(actor, cast).map(
            (one) => one.name || one.id,
          );
          return (
            <Stack key={actor.id} spacing={0.25}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {[actor.name || actor.id, actor.role]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
              <Typography sx={quiet}>
                {R.comesIn(
                  callActorWhen(
                    actor.activation?.trigger || { kind: "always" },
                    actor.channel,
                  ),
                )}
              </Typography>
              {actor.parent && (
                <Typography sx={quiet}>
                  {R.broughtBy(names[actor.parent] || actor.parent)}
                </Typography>
              )}
              <Typography sx={quiet}>
                {reaches.length ? R.reaches(reaches) : R.reachesNoOne}
              </Typography>
            </Stack>
          );
        })}
      </Stack>
    </SectionCard>
  );
}

ActorRelations.propTypes = {
  cast: PropTypes.arrayOf(PropTypes.object).isRequired,
};
