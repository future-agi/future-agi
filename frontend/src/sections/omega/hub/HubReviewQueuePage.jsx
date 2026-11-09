import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import CardActionArea from "@mui/material/CardActionArea";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { KINDS, KIND_LABEL, TIERS, hubPaths } from "./constants";
import { useHubMe, useReviewQueue } from "./api/queries";
import { timeAgo } from "./lib/format";
import { checkTone, statusMeta } from "./lib/status";
import { StatusChip } from "./components/StatusTimeline";
import { HubEmpty, HubError, HubSkeletonCards } from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";
import TierBadge from "./components/TierBadge";
import ToneChip from "./components/ToneChip";

const FILTERS = [
  {
    key: "status",
    label: "Status",
    options: [
      ["in_review", statusMeta("in_review").label],
      ["check_error", statusMeta("check_error").label],
    ],
  },
  {
    key: "kind",
    label: "Kind",
    options: [["", "Any kind"], ...KINDS.map((k) => [k.value, k.label])],
  },
  {
    key: "tier",
    label: "Tier",
    options: [
      ["", "Any tier"],
      ...Object.entries(TIERS).map(([v, m]) => [v, m.label]),
    ],
  },
  {
    key: "update",
    label: "Type",
    options: [
      ["", "New and updates"],
      ["0", "New entries"],
      ["1", "Updates"],
    ],
  },
  {
    key: "flags",
    label: "Flags",
    options: [
      ["", "With or without flags"],
      ["1", "Has flags"],
    ],
  },
  {
    key: "channel",
    label: "Channel",
    options: [
      ["", "Any channel"],
      ["cli", "CLI"],
      ["web", "Web upload"],
      ["github", "GitHub"],
    ],
  },
];

function Row({ row }) {
  const locked =
    row.reviewer &&
    row.reviewStartedAt &&
    Date.now() - new Date(row.reviewStartedAt).getTime() < 2 * 3600 * 1000;
  return (
    <CardActionArea
      component={RouterLink}
      href={hubPaths.reviewItem(row.id)}
      data-testid="hub-queue-row"
      data-ref={row.ref}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.5,
        px: 2,
        py: 1.5,
        flexWrap: "wrap",
      }}
    >
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography
          typography="s1"
          fontWeight={600}
          sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
        >
          {`${row.ref}${row.version ? `@${row.version}` : ""}`}
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {[
            KIND_LABEL[row.kind] || row.kind,
            row.isUpdate ? "update" : "new entry",
            `via ${row.channel}`,
          ]
            .filter(Boolean)
            .join(" · ")}
          {locked ? ` · being reviewed by ${row.reviewer}` : ""}
        </Typography>
      </Box>
      <TierBadge tier={row.tier} short />
      {row.flags > 0 && (
        <ToneChip
          tone={checkTone("flag")}
          label={`${row.flags} flag${row.flags === 1 ? "" : "s"}`}
        />
      )}
      {row.status === "check_error" && <StatusChip status={row.status} />}
      <Typography
        typography="s3"
        color="text.tertiary"
        sx={{ width: 90, textAlign: "right" }}
      >
        {`waiting ${timeAgo(row.createdAt).replace(" ago", "")}`}
      </Typography>
    </CardActionArea>
  );
}

Row.propTypes = { row: PropTypes.object.isRequired };

// Nothing arrives through the CLI channel until a customer can install a CLI
// with `omega hub` (OMEGA_PART HUB_CLI), so that filter value waits for it.
function visibleFilters(cliReleased) {
  if (cliReleased) return FILTERS;
  return FILTERS.map((f) =>
    f.key === "channel"
      ? { ...f, options: f.options.filter(([value]) => value !== "cli") }
      : f,
  );
}

// The review queue (SPEC §5.2): in_review, oldest first, with the filters
// of the spec. Each row shows its age; the target is 2 business days. A value
// the URL names that is not an offered option falls back to the default.
export default function HubReviewQueuePage() {
  const [search, setSearch] = useSearchParams();
  const me = useHubMe();
  const filters = visibleFilters(useOmegaPart(OMEGA_PART.HUB_CLI));
  const params = Object.fromEntries(
    filters.map((f) => {
      const fallback = f.key === "status" ? "in_review" : "";
      const value = search.get(f.key) || fallback;
      return [
        f.key,
        f.options.some(([option]) => option === value) ? value : fallback,
      ];
    }),
  );
  const queue = useReviewQueue(params, Boolean(me.data?.isMaintainer));
  const rows = queue.data?.results || [];
  const set = (key, value) => {
    const next = new URLSearchParams(search);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearch(next, { replace: true });
  };
  return (
    <MaintainerGate
      title="Review queue"
      documentTitle="Review queue"
      description="Submissions that passed the automated checks, oldest first. Nothing is listed until one of you approves it."
      testId="hub-review-queue"
    >
      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        {filters.map((filter) => (
          <TextField
            key={filter.key}
            select
            size="small"
            label={filter.label}
            value={params[filter.key]}
            onChange={(e) => set(filter.key, e.target.value)}
            sx={{ minWidth: 150 }}
          >
            {filter.options.map(([value, label]) => (
              <MenuItem key={value || "any"} value={value}>
                {label}
              </MenuItem>
            ))}
          </TextField>
        ))}
      </Box>
      {queue.isLoading ? (
        <HubSkeletonCards count={3} />
      ) : queue.isError ? (
        <HubError error={queue.error} onRetry={() => queue.refetch()} />
      ) : rows.length === 0 ? (
        <HubEmpty
          title="The queue is empty"
          description="Nothing is waiting for review with these filters."
        />
      ) : (
        <>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="hub-queue-count"
          >
            {`${queue.data.count} waiting`}
          </Typography>
          <Box
            component="ul"
            sx={{
              m: 0,
              p: 0,
              listStyle: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.paper",
            }}
          >
            {rows.map((row, index) => (
              <Box
                component="li"
                key={row.id}
                sx={{
                  borderTop: index ? "1px solid" : 0,
                  borderColor: "divider",
                }}
              >
                <Row row={row} />
              </Box>
            ))}
          </Box>
        </>
      )}
    </MaintainerGate>
  );
}
