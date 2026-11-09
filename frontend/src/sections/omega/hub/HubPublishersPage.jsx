import React from "react";
import Box from "@mui/material/Box";
import CardActionArea from "@mui/material/CardActionArea";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";

import { TIERS, hubPaths } from "./constants";
import { useHubPublishers } from "./api/queries";
import { countOrDash, exactCount, formatDate } from "./lib/format";
import { showsInstalls, useInstallPath } from "./lib/installs";
import { PublisherAvatar } from "./components/EntryCover";
import HubFrame from "./components/HubFrame";
import HubNav from "./components/HubNav";
import { HubEmpty, HubError, HubSkeletonCards } from "./components/HubStates";
import TierBadge from "./components/TierBadge";
import { SortTabsLite } from "./components/SortTabsLite";

// The publishers index (SPEC §7.3): sorted by installs over 30 days,
// filterable by tier. No followers and no activity feed. While nothing can
// install an entry every count is zero, so the page names no installs and the
// server's order falls back to the handle (lib/installs.js).
export default function HubPublishersPage() {
  const installPath = useInstallPath();
  const [search, setSearch] = useSearchParams();
  const tier = search.get("tier") || "";
  const publishers = useHubPublishers({ tier: tier || undefined, sort: "installs", page_size: 60 });
  const rows = publishers.data?.results || [];
  const setTier = (value) => {
    const next = new URLSearchParams(search);
    if (value) next.set("tier", value);
    else next.delete("tier");
    setSearch(next, { replace: true });
  };
  return (
    <HubFrame title="Publishers" documentTitle="Publishers" description={installPath ? "Who publishes on the Hub, by installs over the last 30 days." : "Who publishes on the Hub."} testId="hub-publishers">
      <HubNav />
      <SortTabsLite
        value={tier}
        onChange={setTier}
        options={[["", "All"], ...Object.entries(TIERS).map(([value, meta]) => [value, meta.label])]}
      />
      {publishers.isLoading ? (
        <HubSkeletonCards count={4} />
      ) : publishers.isError ? (
        <HubError error={publishers.error} onRetry={() => publishers.refetch()} />
      ) : rows.length === 0 ? (
        <HubEmpty title="No publishers yet" description="A publisher appears once a maintainer approves its first entry." />
      ) : (
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" } }}>
          {rows.map((p) => (
            <CardActionArea
              key={p.handle}
              component={RouterLink}
              href={hubPaths.publisher(p.handle)}
              data-testid="hub-publisher-row"
              sx={{ p: 2, borderRadius: 1, border: "1px solid", borderColor: "divider", bgcolor: "background.paper", display: "flex", flexDirection: "column", alignItems: "stretch", gap: 1.25 }}
            >
              <Box sx={{ display: "flex", gap: 1.25, alignItems: "center", minWidth: 0 }}>
                <PublisherAvatar publisher={p} size={40} />
                <Box sx={{ minWidth: 0 }}>
                  <Typography typography="s1" fontWeight={600} noWrap>
                    {p.displayName}
                  </Typography>
                  <Typography typography="s2" color="text.secondary" noWrap>
                    {`@${p.handle}`}
                  </Typography>
                </Box>
              </Box>
              <Box>
                <TierBadge tier={p.tier} verifiedAt={p.verifiedAt} entryCount={p.entryCount ?? 0} />
              </Box>
              <Typography typography="s2" color="text.secondary">
                {[
                  `${p.entryCount} entr${p.entryCount === 1 ? "y" : "ies"}`,
                  `${exactCount(p.likes)} likes`,
                  showsInstalls(installPath, p.installs30d) &&
                    `${countOrDash(p.installs30d)} installs (30 days)`,
                  `since ${formatDate(p.firstPublishedAt)}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </CardActionArea>
          ))}
        </Box>
      )}
    </HubFrame>
  );
}
