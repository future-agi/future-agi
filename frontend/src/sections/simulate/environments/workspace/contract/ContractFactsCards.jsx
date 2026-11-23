import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ROW_HEIGHT } from "src/theme/sizes";
import OriginChip from "../../components/OriginChip";
import SectionCard from "../../components/SectionCard";
import ToneChip from "./ToneChip";
import { ACTORS_COPY, MONO_FONT, SUB_GOALS_COPY } from "./contract.constants";

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

function Row({ name, text, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ px: 1.5, minHeight: ROW_HEIGHT.default }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s1",
          fontWeight: 500,
          fontFamily: MONO_FONT,
          width: 220,
          flexShrink: 0,
        }}
      >
        {name}
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        {text}
      </Typography>
      {children}
    </Stack>
  );
}
Row.propTypes = {
  name: PropTypes.string,
  text: PropTypes.string,
  children: PropTypes.node,
};

export function ActorsCard({ actors, sx }) {
  if (!actors?.length) return null;
  return (
    <SectionCard
      title={ACTORS_COPY.title(actors.length)}
      subtitle={ACTORS_COPY.subtitle}
      sx={sx}
    >
      <Rows>
        {actors.map((actor) => (
          <Row key={actor.name} name={actor.name} text={actor.role}>
            {actor.origin && (
              <OriginChip
                origin={actor.origin.kind}
                file={actor.origin.path || actor.origin.field}
                line={actor.origin.line}
              />
            )}
          </Row>
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
  sx: PropTypes.object,
};

export function SubGoalsCard({ subGoals, sx }) {
  if (!subGoals?.length) return null;
  return (
    <SectionCard
      title={SUB_GOALS_COPY.title(subGoals.length)}
      subtitle={SUB_GOALS_COPY.subtitle}
      sx={sx}
    >
      <Rows>
        {subGoals.map((subGoal) => (
          <Row key={subGoal.name} name={subGoal.name} text={subGoal.what}>
            {subGoal.kind && <ToneChip label={subGoal.kind} />}
            {subGoal.output && (
              <ToneChip label={SUB_GOALS_COPY.output(subGoal.output)} />
            )}
            {subGoal.overlay && <ToneChip label={subGoal.overlay} />}
          </Row>
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
  sx: PropTypes.object,
};
