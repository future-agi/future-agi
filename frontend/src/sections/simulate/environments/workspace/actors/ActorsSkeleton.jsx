import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;

// Sized to a real row (ActorRow), so nothing moves when the cast arrives.
function RowSkeleton({ locked }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      alignItems="center"
      sx={{ px: 1.5, py: 1 }}
    >
      <Skeleton
        variant="rounded"
        width={28}
        height={28}
        sx={{ flexShrink: 0, borderRadius: "6px" }}
      />
      <Stack flex={1} minWidth={0}>
        <Skeleton
          variant="rounded"
          width="30%"
          height={12}
          sx={{ my: "4px" }}
        />
        <Skeleton
          variant="rounded"
          width="60%"
          height={10}
          sx={{ my: "3px" }}
        />
      </Stack>
      <Skeleton
        variant="rounded"
        width={72}
        height={16}
        sx={{ borderRadius: "6px" }}
      />
      {!locked && (
        <>
          <Skeleton
            variant="rounded"
            width={24}
            height={24}
            sx={{ borderRadius: "6px" }}
          />
          <Skeleton
            variant="rounded"
            width={24}
            height={24}
            sx={{ borderRadius: "6px" }}
          />
        </>
      )}
    </Stack>
  );
}

RowSkeleton.propTypes = { locked: PropTypes.bool };

function GroupSkeleton({ title, about, rows, locked, addWidth }) {
  return (
    <SectionCard
      title={title}
      subtitle={about}
      sx={{ mb: 2 }}
      action={
        !locked && (
          <Skeleton
            variant="rounded"
            width={addWidth}
            height={28}
            sx={{ flexShrink: 0, borderRadius: "6px" }}
          />
        )
      }
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {Array.from({ length: rows }, (_, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <RowSkeleton key={i} locked={locked} />
        ))}
      </Stack>
    </SectionCard>
  );
}

GroupSkeleton.propTypes = {
  title: PropTypes.string,
  about: PropTypes.string,
  rows: PropTypes.number,
  locked: PropTypes.bool,
  addWidth: PropTypes.number,
};

/** The tab while its cast loads: the words are known, so only the rows are placeholders. */
export default function ActorsSkeleton({ callerSide = false, locked = false }) {
  return (
    <Box
      role="progressbar"
      aria-busy="true"
      aria-label={C.loading}
      sx={{ p: 2 }}
    >
      <Typography
        sx={{ typography: "s2", color: "text.secondary", maxWidth: 780, mb: 2 }}
      >
        {C.intro}
      </Typography>
      {callerSide && (
        <GroupSkeleton
          title={C.callerSide}
          about={C.callerSideAbout}
          rows={1}
          locked={locked}
          addWidth={210}
        />
      )}
      <GroupSkeleton
        title={C.worldSide}
        about={C.worldSideAbout}
        rows={2}
        locked={locked}
        addWidth={174}
      />
    </Box>
  );
}

ActorsSkeleton.propTypes = {
  callerSide: PropTypes.bool,
  locked: PropTypes.bool,
};
