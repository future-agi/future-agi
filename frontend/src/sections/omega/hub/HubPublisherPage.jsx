import React, { useEffect } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { useNavigate, useParams } from "react-router-dom";

import { hubPaths } from "./constants";
import { useHubPublisher } from "./api/queries";
import { countIfAny, formatDate } from "./lib/format";
import { showsInstalls, useInstallPath } from "./lib/installs";
import DiscordLink from "./components/DiscordLink";
import { EntryGrid } from "./components/EntryCard";
import { PublisherAvatar } from "./components/EntryCover";
import HubFrame from "./components/HubFrame";
import HubIcon from "./components/HubIcon";
import { HubEmpty, HubError, HubSkeletonPage, HubState } from "./components/HubStates";
import TierBadge from "./components/TierBadge";

function Stat({ label, value, testId }) {
  return (
    <Box data-testid={testId}>
      <Typography typography="m3" fontWeight={600}>
        {value}
      </Typography>
      <Typography typography="s3" color="text.secondary">
        {label}
      </Typography>
    </Box>
  );
}

Stat.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  testId: PropTypes.string,
};

// The profile's facts. A count shows once it is above zero (lib/format.js
// countIfAny), as on the cards and the publishers index: a new publisher
// reads "11 Entries · 11 Nov 2026 First published", never "0 Likes" or
// "— Installs". Installs also need a way to install, or a count the server
// did see (lib/installs.js). The date shows once there is one.
function profileStats(p, installPath) {
  const entryCount = countIfAny(p.entryCount);
  const likeCount = countIfAny(p.likes);
  const installCount = showsInstalls(installPath, p.installs30d)
    ? countIfAny(p.installs30d)
    : null;
  return [
    entryCount && {
      key: "entries",
      label: Number(p.entryCount) === 1 ? "Entry" : "Entries",
      value: entryCount,
    },
    likeCount && {
      key: "likes",
      label: Number(p.likes) === 1 ? "Like" : "Likes",
      value: likeCount,
    },
    installCount && {
      key: "installs",
      label: "Installs, last 30 days",
      value: installCount,
    },
    p.firstPublishedAt && {
      key: "first-published",
      label: "First published",
      value: formatDate(p.firstPublishedAt),
    },
  ].filter(Boolean);
}

// A publisher's profile (SPEC §6.5): tier, entries, total likes, installs
// over 30 days (once they can be counted, lib/installs.js) and the first
// published date, each only when it has something to say; with nothing to
// say there is no band of dashes. No followers, no feed.
export default function HubPublisherPage() {
  const installPath = useInstallPath();
  const { handle } = useParams();
  const navigate = useNavigate();
  const publisher = useHubPublisher(handle);
  const p = publisher.data;
  const stats = p ? profileStats(p, installPath) : [];
  const back = { href: hubPaths.publishers, label: "Publishers" };
  useEffect(() => {
    const moved = publisher.error?.movedTo || p?.movedTo || (p?.handle && p.handle !== handle ? p.handle : null);
    if (moved) navigate(hubPaths.publisher(moved), { replace: true });
  }, [publisher.error, p, handle, navigate]);

  if (publisher.isLoading) {
    return (
      <HubFrame back={back} documentTitle={handle}>
        <HubSkeletonPage />
      </HubFrame>
    );
  }
  if (publisher.error?.status === 404 || publisher.error?.status === 410) {
    return (
      <HubFrame back={back} documentTitle={handle}>
        <HubState icon="search" title={publisher.error.status === 410 ? "This publisher is suspended" : "No such publisher"} description={`@${handle}`} />
      </HubFrame>
    );
  }
  if (publisher.isError || !p) {
    return (
      <HubFrame back={back} documentTitle={handle}>
        <HubError error={publisher.error} onRetry={() => publisher.refetch()} />
      </HubFrame>
    );
  }
  return (
    <HubFrame back={back} documentTitle={p.displayName} testId="hub-publisher">
      <Box sx={{ display: "flex", gap: 2, alignItems: "flex-start", flexWrap: "wrap" }}>
        <PublisherAvatar publisher={p} size={64} />
        <Box sx={{ flex: 1, minWidth: 220, display: "flex", flexDirection: "column", gap: 0.5 }}>
          <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
            <Typography component="h1" typography="m2" fontWeight={600}>
              {p.displayName}
            </Typography>
            <TierBadge tier={p.tier} verifiedAt={p.verifiedAt} entryCount={p.entryCount ?? 0} />
          </Box>
          <Typography typography="s2" color="text.secondary">
            {`@${p.handle}`}
            {p.tier === "verified" && p.verifiedAt ? ` · Verified publisher · ${formatDate(p.verifiedAt)}` : ""}
          </Typography>
          {p.bio && (
            <Typography typography="s1" color="text.primary">
              {p.bio}
            </Typography>
          )}
          <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
            {p.website && (
              <Link href={p.website} target="_blank" rel="nofollow noopener noreferrer" typography="s2" sx={{ display: "inline-flex", gap: 0.5, alignItems: "center" }}>
                <HubIcon name="globe" size={14} />
                {p.website.replace(/^https:\/\//, "")}
              </Link>
            )}
            {p.githubLogin && (
              <Link href={`https://github.com/${encodeURIComponent(p.githubLogin)}`} target="_blank" rel="nofollow noopener noreferrer" typography="s2" sx={{ display: "inline-flex", gap: 0.5, alignItems: "center" }}>
                <HubIcon name="github" size={14} />
                {p.githubLogin}
              </Link>
            )}
          </Box>
        </Box>
        <DiscordLink variant="outlined" />
      </Box>
      {stats.length > 0 && (
        <Box
          data-testid="hub-publisher-stats"
          sx={{ display: "flex", gap: 4, flexWrap: "wrap", py: 1.5, borderTop: "1px solid", borderBottom: "1px solid", borderColor: "divider" }}
        >
          {stats.map((stat) => (
            <Stat
              key={stat.key}
              label={stat.label}
              value={stat.value}
              testId={`hub-publisher-stat-${stat.key}`}
            />
          ))}
        </Box>
      )}
      {(p.entries || []).length ? (
        <EntryGrid entries={p.entries} />
      ) : (
        <HubEmpty title="No listed entries" description="Entries appear here once a maintainer approves them." />
      )}
    </HubFrame>
  );
}
