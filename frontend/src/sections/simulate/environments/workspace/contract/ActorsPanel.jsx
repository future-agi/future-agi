import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, IconButton, Tooltip, Collapse, Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  ACTOR_LIBRARY, castFor, getPressure, getEntry,
} from "src/api/simulate-environments/_fixtures/actors";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import { Label } from "./ContractPart";
import { CONTRACT_COPY } from "./contract.constants";

const C = CONTRACT_COPY.actors;

/**
 * Actors — the contract's third-party slot. A persona is who the agent is
 * serving; an actor is someone else in the world who wants something different.
 * Every row leads with the actor's goal, because the goal is the whole
 * mechanism, and states the pressure kind once — on the role chip, the only
 * mark whose colour says what the colour means.
 *
 * Presentational: it reads the cast from the environment (an explicit
 * `envState.actors` id list, or the modality-fit default from `castFor`) and
 * renders it. Creating, editing and removing actors need a `patch` channel the
 * contract tab does not expose, so those affordances are not ported here.
 */
export default function ActorsPanel({ env, envState, onGo }) {
  const cast = envState?.actors || castFor(env);

  const inCast = useMemo(() => {
    const set = new Set(cast);
    return ACTOR_LIBRARY.filter((a) => set.has(a.id));
  }, [cast]);

  return (
    <Box>
      <Box sx={{ mb: 2 }}>
        <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>{C.heading}</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}>
          {C.headingBlurb}
        </Typography>
      </Box>

      <Box
        sx={{
          p: 1.75, mb: 2, borderRadius: 1.25, border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Stack direction="row" spacing={1.25} alignItems="flex-start">
          <Iconify icon="solar:lightbulb-linear" width={16} sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box component="span" sx={{ fontWeight: "fontWeightBold", color: "text.primary" }}>{C.calloutLead}</Box>{" "}
            {C.calloutBody}{" "}
            <Box
              component="span"
              onClick={() => onGo?.("scenarios")}
              sx={{ color: "primary.main", fontWeight: "fontWeightBold", cursor: "pointer" }}
            >
              {C.calloutLink}
            </Box>
          </Typography>
        </Stack>
      </Box>

      <SectionCard title={C.castTitle(inCast.length)} subtitle={C.castSubtitle}>
        {inCast.length === 0 ? (
          <EmptyState icon={C.emptyIcon} title={C.emptyTitle} body={C.emptyBody} />
        ) : (
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {inCast.map((a) => (
              <ActorRow key={a.id} actor={a} />
            ))}
          </Stack>
        )}
      </SectionCard>
    </Box>
  );
}

const ACTOR_SHAPE = PropTypes.shape({
  id: PropTypes.string.isRequired,
  name: PropTypes.string,
  goal: PropTypes.string,
  blurb: PropTypes.string,
  tests: PropTypes.string,
  entry: PropTypes.string,
  pressure: PropTypes.string,
  modalities: PropTypes.arrayOf(PropTypes.string),
  traits: PropTypes.arrayOf(PropTypes.string),
  version: PropTypes.string,
  versions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string, note: PropTypes.string })),
  usedBy: PropTypes.number,
  owner: PropTypes.string,
});

ActorsPanel.propTypes = {
  env: PropTypes.shape({ surface: PropTypes.string }).isRequired,
  envState: PropTypes.shape({ actors: PropTypes.arrayOf(PropTypes.string) }).isRequired,
  onGo: PropTypes.func,
};

function ActorRow({ actor }) {
  const [open, setOpen] = useState(false);
  const pressure = getPressure(actor.pressure);
  const entry = getEntry(actor.entry);
  const toggle = () => setOpen((o) => !o);

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
        <Tooltip arrow title={pressure.blurb}>
          <Box
            onClick={toggle}
            sx={{
              width: 30, height: 30, borderRadius: 0.875, flexShrink: 0, cursor: "pointer",
              display: "grid", placeItems: "center", color: "text.subtitle", bgcolor: "background.neutral",
            }}
          >
            <Iconify icon="solar:users-group-two-rounded-linear" width={16} />
          </Box>
        </Tooltip>

        <Box flex={1} minWidth={0} onClick={toggle} sx={{ cursor: "pointer" }}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{actor.name}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>· {actor.version}</Typography>
            {actor.owner === "system" && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>· {C.builtIn}</Typography>
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            <Box component="span" sx={{ color: "text.primary", fontWeight: "fontWeightBold" }}>{C.wants}</Box> {actor.goal}
          </Typography>
        </Box>

        <Chip
          size="small"
          label={pressure.label}
          sx={{
            height: 20, borderRadius: 0.75, flexShrink: 0, color: pressure.color,
            border: "1px solid", borderColor: alpha(pressure.color, 0.4), bgcolor: "transparent",
            display: { xs: "none", md: "flex" },
            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: "fontWeightSemiBold" },
          }}
        />

        <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0, display: { xs: "none", md: "flex" } }}>
          {(actor.modalities || []).map((m) => (
            <ModalityChip key={m} label={m} />
          ))}
        </Stack>

        <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, display: { xs: "none", lg: "block" }, width: 118 }}>
          {entry.label.toLowerCase()}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, display: { xs: "none", sm: "block" } }}>
          {C.usedBy(actor.usedBy)}
        </Typography>

        <Tooltip arrow title={open ? "Collapse" : "Expand"}>
          <IconButton size="small" onClick={toggle} sx={{ flexShrink: 0 }}>
            <Iconify icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={16} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Tooltip>
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack spacing={1.5} sx={{ px: 2.5, pb: 2, pl: 7 }}>
          <DetailBlock label={C.detail.does} body={actor.blurb} />
          <DetailBlock label={C.detail.tests} body={actor.tests} />
          <DetailBlock label={C.detail.entry} body={`${entry.label} — ${entry.blurb}`} />
          <Box>
            <Label>{C.detail.modalities}</Label>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {(actor.modalities || []).map((m) => (
                <ModalityChip key={m} label={m} />
              ))}
            </Stack>
          </Box>
          <Box>
            <Label>{C.detail.traits}</Label>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {(actor.traits || []).map((t) => (
                <Chip
                  key={t} size="small" label={t}
                  sx={{
                    height: 20, borderRadius: 0.75, color: "text.secondary",
                    border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                    "& .MuiChip-label": { px: 0.75, typography: "s3" },
                  }}
                />
              ))}
            </Stack>
          </Box>
          <Box>
            <Label>{C.detail.versions}</Label>
            <Stack spacing={0.75}>
              {(actor.versions || []).map((v) => (
                <Stack key={v.label} direction="row" spacing={1.5} alignItems="flex-start">
                  <Typography sx={{ width: 28, flexShrink: 0, typography: "s2", fontWeight: "fontWeightBold" }}>{v.label}</Typography>
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>{v.note}</Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        </Stack>
      </Collapse>
    </Box>
  );
}
ActorRow.propTypes = { actor: ACTOR_SHAPE };

function ModalityChip({ label }) {
  return (
    <Chip
      size="small" label={label}
      sx={{
        height: 20, borderRadius: 0.75, color: "text.subtitle",
        border: "1px solid", borderColor: "divider", bgcolor: "transparent",
        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: "fontWeightMedium" },
      }}
    />
  );
}
ModalityChip.propTypes = { label: PropTypes.string };

function DetailBlock({ label, body }) {
  return (
    <Box>
      <Label>{label}</Label>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{body}</Typography>
    </Box>
  );
}
DetailBlock.propTypes = { label: PropTypes.node, body: PropTypes.node };
