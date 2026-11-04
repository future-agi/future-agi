import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;

// Sized to a real one-line row (ActorRow), so a short cast doesn't jump when it arrives.
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

function GroupSkeleton({ title, about, locked, addWidth, children }) {
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
        {children}
      </Stack>
    </SectionCard>
  );
}

GroupSkeleton.propTypes = {
  title: PropTypes.string,
  about: PropTypes.string,
  locked: PropTypes.bool,
  addWidth: PropTypes.number,
  children: PropTypes.node,
};

/** The tab while its cast loads: the words are known, so only the rows are placeholders. */
export default function ActorsSkeleton({ callerSide = false, locked = false }) {
  return (
    <Box
      role="progressbar"
      aria-busy="true"
      aria-live="polite"
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
          locked={locked}
          addWidth={210}
        >
          <RowSkeleton locked={locked} />
        </GroupSkeleton>
      )}
      <GroupSkeleton
        title={C.worldSide}
        about={C.worldSideAbout}
        locked={locked}
        addWidth={174}
      >
        <RowSkeleton locked={locked} />
        <RowSkeleton locked={locked} />
      </GroupSkeleton>
    </Box>
  );
}

ActorsSkeleton.propTypes = {
  callerSide: PropTypes.bool,
  locked: PropTypes.bool,
};
