// A machine / agent connection pill (R4-SPEC D21; implementation; frozen props +
// optional `machine`, `link`). `● alice@mbp-14 · Alice Park · online`,
// `◐ ci-runner-7 · Workspace runner · last seen 2h ago`, `▲ … · update
// needed`, `[lock] ravi@thinkpad · only Ravi Menon`, `○ … · offline`; your
// own reads `maya@macbook-air · you · online`. The shape carries the state (never
// colour alone). dataset integration FE-0: the Runners page is absent, so the pill is
// never a link (`link` is accepted and ignored). One shared machines query
// (`useMachines`, 5 s).
import React from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import { NOT_IN_RELEASE_REASON } from "src/sections/common/RunSources/runSourceAvailability";
import { useMachine } from "./useConnect";
import { pillHelp, pillOf } from "./connectCopy";

const SIZES = {
  small: { h: 22, type: "s3", glyph: 8, icon: 13 },
  medium: { h: 26, type: "s2", glyph: 9, icon: 14 },
};

/** The state glyph: filled dot, half ring, triangle, lock, hollow ring. */
export function StatusGlyph({ tone, size = 8, icon = 13 }) {
  if (tone === "locked")
    return (
      <Iconify
        icon="mdi:lock-outline"
        width={icon}
        sx={{ color: "text.secondary", flexShrink: 0 }}
        aria-hidden
      />
    );
  if (tone === "update")
    return (
      <Iconify
        icon="mdi:alert"
        width={icon}
        sx={{ color: "warning.main", flexShrink: 0 }}
        aria-hidden
      />
    );
  const base = {
    width: size,
    height: size,
    borderRadius: "50%",
    flexShrink: 0,
    boxSizing: "border-box",
  };
  if (tone === "online")
    return <Box aria-hidden sx={{ ...base, bgcolor: "success.main" }} />;
  if (tone === "stale")
    return (
      <Box
        aria-hidden
        sx={{
          ...base,
          border: "1.5px solid",
          borderColor: "warning.main",
          background: (t) =>
            `linear-gradient(90deg, ${t.palette.warning.main} 50%, transparent 50%)`,
        }}
      />
    );
  return (
    <Box
      aria-hidden
      sx={{ ...base, border: "1.5px solid", borderColor: "text.disabled" }}
    />
  );
}
StatusGlyph.propTypes = {
  tone: PropTypes.string,
  size: PropTypes.number,
  icon: PropTypes.number,
};

const TONE_LABEL = {
  online: "Online",
  stale: "Stale",
  update: "Update needed",
  locked: "You can't run here",
  offline: "Offline",
  unknown: "Not connected",
};

export default function ConnectionPill({
  machineId,
  agentId,
  size = "small",
  showLastSeen = false,
  showOwner = true,
  machine: given,
  sx,
}) {
  const { machine: fetched, isLoading, held } = useMachine({
    machineId,
    agentId,
    enabled: !given,
  });
  const machine = given ?? fetched;
  // This build's backend does not serve the machines list (no request is
  // sent): the pill stays and says so instead of "Not connected".
  const notInRelease = !given && held;
  const S = SIZES[size] ?? SIZES.small;
  if (!machine && isLoading)
    return (
      <Skeleton
        variant="rounded"
        width={size === "medium" ? 200 : 168}
        height={S.h}
        sx={{ display: "inline-block", verticalAlign: "middle" }}
      />
    );
  const p = notInRelease
    ? {
        tone: "unknown",
        name: NOT_IN_RELEASE_REASON,
        owner: null,
        status: null,
        lastSeen: false,
      }
    : pillOf(machine, { showOwner, showLastSeen });
  const id = machine?.id ?? machineId;
  const label = [p.name, p.owner, p.status].filter(Boolean).join(" · ");
  const body = (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      sx={{ minWidth: 0, whiteSpace: "nowrap" }}
    >
      <StatusGlyph tone={p.tone} size={S.glyph} icon={S.icon} />
      <Typography
        component="span"
        typography={S.type}
        sx={{
          color: "text.primary",
          fontWeight: "fontWeightMedium",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {p.name}
      </Typography>
      {p.owner ? (
        <Typography component="span" typography={S.type} color="text.secondary">
          · {p.owner}
        </Typography>
      ) : null}
      {p.status ? (
        <Typography component="span" typography={S.type} color="text.secondary">
          · {p.status}
          {p.lastSeen && machine?.last_seen_at ? (
            <>
              {" "}
              <RelativeTime iso={machine.last_seen_at} />
            </>
          ) : null}
        </Typography>
      ) : null}
    </Stack>
  );
  const tip = (
    <Stack gap={0.25} sx={{ py: 0.25 }}>
      <Typography typography="s2" fontWeight="fontWeightSemiBold">
        {TONE_LABEL[p.tone]} · {p.name}
      </Typography>
      {(notInRelease ? [] : pillHelp(machine)).map((l) => (
        <Typography key={l} typography="s3">
          {l}
        </Typography>
      ))}
    </Stack>
  );
  const shell = {
    display: "inline-flex",
    alignItems: "center",
    height: S.h,
    maxWidth: "100%",
    px: 1,
    border: "1px solid",
    borderColor: "divider",
    borderRadius: S.h / 2,
    bgcolor: "background.paper",
    verticalAlign: "middle",
    ...sx,
  };
  return (
    <CustomTooltip show title={tip} placement="top" size="small">
      <Box
        component="span"
        aria-label={label}
        data-connection-pill={id ?? ""}
        data-pill-tone={p.tone}
        sx={shell}
      >
        {body}
      </Box>
    </CustomTooltip>
  );
}

ConnectionPill.propTypes = {
  machineId: PropTypes.string,
  agentId: PropTypes.string,
  size: PropTypes.oneOf(["small", "medium"]),
  showLastSeen: PropTypes.bool,
  showOwner: PropTypes.bool,
  /** Additive (implementation): render this machine without a lookup. */
  machine: PropTypes.object,
  /** Additive (implementation): false = not a link (inside a link already). */
  link: PropTypes.bool,
  sx: PropTypes.object,
};
