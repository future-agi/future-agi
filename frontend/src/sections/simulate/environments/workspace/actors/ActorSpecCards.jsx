import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;
const S = ACTORS_COPY.spec;
const CHANNEL_LABELS = ACTORS_COPY.callActors.channels;
const MONO = "ui-monospace, Menlo, monospace";

function SpecCard({ title, children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          px: 1.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}

SpecCard.propTypes = { title: PropTypes.string, children: PropTypes.node };

function SpecRow({ label, children }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "112px minmax(0, 1fr)",
        columnGap: 1.5,
        alignItems: "baseline",
        px: 1.5,
        py: 0.875,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
        {label}
      </Typography>
      <Box
        sx={{
          typography: "s2",
          color: "text.secondary",
          minWidth: 0,
          wordBreak: "break-word",
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

SpecRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

function Lines({ items }) {
  return (
    <Stack spacing={0.25}>
      {items.map((one) => (
        <Typography
          key={one}
          sx={{ typography: "s2", color: "text.secondary" }}
        >
          {one}
        </Typography>
      ))}
    </Stack>
  );
}

Lines.propTypes = { items: PropTypes.arrayOf(PropTypes.string).isRequired };

function Chips({ items }) {
  return (
    <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.5 }}>
      {items.map((one) => (
        <Box
          key={one}
          component="span"
          sx={{
            px: 0.75,
            minHeight: 20,
            display: "inline-flex",
            alignItems: "center",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            typography: "s3",
            color: "text.secondary",
          }}
        >
          {one}
        </Box>
      ))}
    </Stack>
  );
}

Chips.propTypes = { items: PropTypes.arrayOf(PropTypes.string).isRequired };

const answersWithin = (timing) => {
  if (timing?.kind !== "delayed" || !timing.seconds) return null;
  return S.answersWithin(timing.seconds);
};

const traitsOf = (actor) =>
  [
    actor.behaviour?.style,
    actor.persona?.personality,
    actor.persona?.communication_style,
  ]
    .flatMap((text) => String(text || "").split(/[,;]/))
    .map((text) => text.trim())
    .filter(Boolean)
    .filter((text, index, all) => all.indexOf(text) === index);

const voiceOf = (actor, loudChannels) => {
  if (!actor.channel || !loudChannels.includes(actor.channel)) return null;
  return [
    actor.persona?.gender,
    actor.persona?.age_group,
    actor.persona?.accent,
    actor.loudness_ratio
      ? C.loudnessShare(actor.loudness_ratio)
      : C.loudnessAutomatic,
  ]
    .filter(Boolean)
    .join(" · ");
};

const neverSeesOf = (view) => [
  ...(view.never_sees.grader.length
    ? [`${C.graderMaterial}: ${view.never_sees.grader.join(", ")}`]
    : [S.graderAlways]),
  S.hiddenTask,
  ...(view.never_sees.others.length
    ? view.never_sees.others.map((one) =>
        C.othersPrivate(one.fields.join(", "), one.count),
      )
    : [S.othersAlways]),
];

export function TheirPart({ actor, loudChannels = [] }) {
  const caller = actor.side === "caller";
  const mayAgree = actor.constraints?.may_agree?.filter(Boolean) || [];
  const within = answersWithin(actor.behaviour?.response_timing);
  const traits = traitsOf(actor);
  const voice = voiceOf(actor, loudChannels);
  return (
    <SpecCard title={S.theirPart}>
      {actor.goal && <SpecRow label={S.tests}>{actor.goal}</SpecRow>}
      {caller && actor.channel && (
        <SpecRow label={S.howTheyJoin}>
          {CHANNEL_LABELS[actor.channel] || actor.channel}
        </SpecRow>
      )}
      <SpecRow label={S.whenTheyComeIn}>{actor.when_label}</SpecRow>
      {!caller && (
        <SpecRow label={S.actions}>
          {actor.permissions.length ? (
            <Box component="span" sx={{ fontFamily: MONO }}>
              {actor.permissions
                .map((one) =>
                  one.in_world ? one.action : `${one.action} (${C.notInWorld})`,
                )
                .join(", ")}
            </Box>
          ) : (
            S.onlyTalk
          )}
        </SpecRow>
      )}
      {voice && <SpecRow label={S.voice}>{voice}</SpecRow>}
      {mayAgree.length > 0 && (
        <SpecRow label={S.canApprove}>
          {[mayAgree.join("; "), within].filter(Boolean).join(" · ")}
        </SpecRow>
      )}
      {actor.fallback?.label && (
        <SpecRow label={S.ifNoAnswer}>{actor.fallback.label}</SpecRow>
      )}
      {traits.length > 0 && (
        <SpecRow label={S.traits}>
          <Chips items={traits} />
        </SpecRow>
      )}
    </SpecCard>
  );
}

TheirPart.propTypes = {
  actor: PropTypes.object.isRequired,
  loudChannels: PropTypes.arrayOf(PropTypes.string),
};

export function WhatTheySee({ actor, view }) {
  return (
    <SpecCard title={S.whatTheySee(actor.name)}>
      {view.unused_reason && (
        <SpecRow label={S.note}>{view.unused_reason}</SpecRow>
      )}
      {view.sees.map((one) => (
        <SpecRow
          key={one.input}
          label={one.label || C.inputs[one.input] || one.input}
        >
          {one.text || (
            <Box component="span" sx={{ color: "text.disabled" }}>
              {C.notSet}
            </Box>
          )}
        </SpecRow>
      ))}
      <SpecRow label={C.neverSees}>
        <Lines items={neverSeesOf(view)} />
      </SpecRow>
    </SpecCard>
  );
}

WhatTheySee.propTypes = {
  actor: PropTypes.object.isRequired,
  view: PropTypes.shape({
    unused_reason: PropTypes.string,
    sees: PropTypes.arrayOf(
      PropTypes.shape({
        input: PropTypes.string.isRequired,
        label: PropTypes.string,
        text: PropTypes.string,
      }),
    ).isRequired,
    never_sees: PropTypes.shape({
      grader: PropTypes.arrayOf(PropTypes.string).isRequired,
      others: PropTypes.arrayOf(
        PropTypes.shape({
          fields: PropTypes.arrayOf(PropTypes.string).isRequired,
          count: PropTypes.number.isRequired,
        }),
      ).isRequired,
    }).isRequired,
  }).isRequired,
};

export function WhatTheySeeSkeleton({ actor }) {
  return (
    <SpecCard title={S.whatTheySee(actor.name)}>
      {[
        ["56%", 1],
        ["44%", 2],
        ["62%", 3],
      ].map(([width, key]) => (
        <SpecRow key={key} label={S.loadingLabels[key - 1]}>
          <Skeleton variant="rounded" width={width} height={12} />
        </SpecRow>
      ))}
    </SpecCard>
  );
}

WhatTheySeeSkeleton.propTypes = { actor: PropTypes.object.isRequired };
