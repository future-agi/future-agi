import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";

import { hubPaths } from "../constants";
import { useHubPublisher } from "../api/queries";

import { PublisherAvatar } from "./EntryCover";
import { HubSection } from "./HubStates";
import TierBadge from "./TierBadge";

// The publisher card in an entry's right rail: avatar, name, tier, entry
// count and a link to the profile (SPEC §7.2).
export default function PublisherCard({ publisher }) {
  const { data } = useHubPublisher(publisher?.handle);
  const profile = data || publisher || {};
  return (
    <HubSection title="Publisher" testId="hub-publisher-card">
      <Box sx={{ display: "flex", gap: 1.25, alignItems: "center", minWidth: 0 }}>
        <PublisherAvatar publisher={profile} size={36} />
        <Box sx={{ minWidth: 0 }}>
          <Link
            component={RouterLink}
            href={hubPaths.publisher(profile.handle)}
            typography="s1"
            fontWeight={600}
            color="text.primary"
            underline="hover"
          >
            {profile.displayName || profile.handle}
          </Link>
          <Typography typography="s2" color="text.secondary">
            {`@${profile.handle}`}
            {typeof profile.entryCount === "number"
              ? ` · ${profile.entryCount} entr${profile.entryCount === 1 ? "y" : "ies"}`
              : ""}
          </Typography>
        </Box>
      </Box>
      <Box>
        <TierBadge tier={profile.tier} verifiedAt={profile.verifiedAt} entryCount={profile.entryCount} />
      </Box>
    </HubSection>
  );
}

PublisherCard.propTypes = { publisher: PropTypes.object };
