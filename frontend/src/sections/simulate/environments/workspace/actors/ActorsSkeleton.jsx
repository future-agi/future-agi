import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;

const VISUALLY_HIDDEN = {
  position: "absolute",
  width: "1px",
  height: "1px",
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};

// Sized to a row with no Wants line (ActorRow), so a short cast doesn't jump much when it arrives.
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
export default function ActorsSkeleton({ locked = false }) {
  return (
    <Box aria-busy="true" sx={{ p: 2 }}>
      <Box role="status" sx={VISUALLY_HIDDEN}>
        {C.loading}
      </Box>
      <Typography
        sx={{ typography: "s2", color: "text.secondary", maxWidth: 780, mb: 2 }}
      >
        {C.intro}
      </Typography>
      <GroupSkeleton
        title={C.title}
        about={C.everyoneAbout}
        locked={locked}
        addWidth={140}
      >
        <RowSkeleton locked={locked} />
        <RowSkeleton locked={locked} />
      </GroupSkeleton>
    </Box>
  );
}

ActorsSkeleton.propTypes = {
  locked: PropTypes.bool,
};
