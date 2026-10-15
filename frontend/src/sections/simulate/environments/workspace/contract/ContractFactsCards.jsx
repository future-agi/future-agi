import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import OriginChip from "../../components/OriginChip";
import SectionCard from "../../components/SectionCard";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import ToneChip from "./ToneChip";
import { ACTORS_COPY, MONO_FONT, SUB_GOALS_COPY } from "./contract.constants";

function CountLabel({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        color: "text.subtitle",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {children}
    </Typography>
  );
}
CountLabel.propTypes = { children: PropTypes.node };

function Rows({ children }) {
  return (
    <Stack
      divider={
        <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
      }
    >
      {children}
    </Stack>
  );
}
Rows.propTypes = { children: PropTypes.node };

export function ActorsCard({ actors }) {
  if (!actors?.length) return null;
  return (
    <SectionCard
      title={ACTORS_COPY.title}
      subtitle={ACTORS_COPY.subtitle}
      action={<CountLabel>{ACTORS_COPY.count(actors.length)}</CountLabel>}
      sx={{ mb: 2 }}
    >
      <Rows>
        {actors.map((actor) => (
          <Stack
            key={actor.name}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1.375 }}
          >
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontFamily: MONO_FONT,
                minWidth: 180,
              }}
            >
              {actor.name}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                flex: 1,
                minWidth: 0,
              }}
              noWrap
            >
              {actor.role}
            </Typography>
            {actor.origin && (
              <OriginChip
                origin={actor.origin.kind}
                file={actor.origin.path || actor.origin.field}
                line={actor.origin.line}
              />
            )}
          </Stack>
        ))}
      </Rows>
    </SectionCard>
  );
}
ActorsCard.propTypes = {
  actors: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      role: PropTypes.string,
      origin: PropTypes.object,
    }),
  ),
};

export function SubGoalsCard({ subGoals }) {
  if (!subGoals?.length) return null;
  return (
    <SectionCard
      title={SUB_GOALS_COPY.title}
      subtitle={SUB_GOALS_COPY.subtitle}
      action={<CountLabel>{SUB_GOALS_COPY.count(subGoals.length)}</CountLabel>}
      sx={{ mb: 2 }}
    >
      <Rows>
        {subGoals.map((subGoal) => (
          <Stack
            key={subGoal.name}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1.375 }}
          >
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontFamily: MONO_FONT,
                minWidth: 180,
              }}
            >
              {subGoal.name}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                flex: 1,
                minWidth: 0,
              }}
              noWrap
            >
              {subGoal.what}
            </Typography>
            {subGoal.kind && (
              <ToneChip label={subGoal.kind} color={BUILD_TONES.grey} />
            )}
            {subGoal.output && (
              <ToneChip
                label={SUB_GOALS_COPY.output(subGoal.output)}
                color={BUILD_TONES.blue}
              />
            )}
            {subGoal.overlay && (
              <ToneChip label={subGoal.overlay} color={BUILD_TONES.accent} />
            )}
          </Stack>
        ))}
      </Rows>
    </SectionCard>
  );
}
SubGoalsCard.propTypes = {
  subGoals: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      what: PropTypes.string,
      kind: PropTypes.string,
      output: PropTypes.string,
      overlay: PropTypes.string,
    }),
  ),
};
