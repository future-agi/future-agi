import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import OriginChip from "../../components/OriginChip";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "./overview.constants";
import {
  MAP_COPY,
  toolMapRows,
  ruleMapRows,
  storeMapRows,
  actorMapRows,
} from "./sourceToSandbox.constants";
import { InlineResolve, NeedsAnswerLabel, ConfirmedChip } from "./SourceToSandboxResolve";

const MONO = "ui-monospace, Menlo, monospace";
// Shared grid template so column headers, group headers and every row land on
// the same tracks; min-content right-aligns each origin chip to its widest peer.
const MAP_GRID = "minmax(0, 1fr) min-content 24px minmax(0, 1fr)";
const MAP_PX = 3;

// A two-column ledger: what was read from source, and what it became in the
// sandbox. Rows the reader could not classify from static analysis carry an
// inline resolve control anchored below the fact it belongs to. This is the
// artifact someone returns to weeks later to see which decisions a human made
// and which the reader made itself.
export default function SourceToSandboxMap({ env, envState, patch }) {
  const toolRows = toolMapRows(env, envState);
  const ruleRows = ruleMapRows(env);
  const storeRows = storeMapRows(env);
  const actors = actorMapRows(env);

  const toAnswer = toolRows.filter((r) => r.isUnresolved).length;
  const actorMapped = actors.filter((a) => a.mapped).length;

  const resolutions = envState?.toolResolutions || {};
  const setToolResolution = (name, choice) =>
    patch?.({ toolResolutions: { ...resolutions, [name]: choice } });

  return (
    <Box
      sx={{
        mb: 3, borderRadius: 2, overflow: "hidden",
        border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
      }}
    >
      <Box sx={{ px: MAP_PX, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>{MAP_COPY.title}</Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25, maxWidth: 720 }}>
          {MAP_COPY.subtitle}
        </Typography>
      </Box>

      <Box
        sx={{
          display: "grid", gridTemplateColumns: MAP_GRID, columnGap: 2,
          alignItems: "center", px: MAP_PX, py: 1.25,
          borderBottom: "1px solid", borderColor: "divider",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02),
        }}
      >
        <ColHead>{MAP_COPY.readHead}</ColHead>
        <Box />
        <Box />
        <ColHead>{MAP_COPY.sandboxHead}</ColHead>
      </Box>

      <MapGroup
        label={MAP_COPY.groups.tools.label}
        hint={MAP_COPY.groups.tools.hint}
        right={MAP_COPY.toolsRight(toolRows.length, toAnswer)}
        toAnswer={toAnswer}
        first
      >
        {toolRows.map((r, i) => (
          <MapRow
            key={r.key}
            index={i}
            name={r.name}
            origin={r.origin}
            target={r.isUnresolved ? <NeedsAnswerLabel /> : r.target}
            mono
            highlight={r.isUnresolved}
            trailing={r.confirmed && <ConfirmedChip />}
            below={r.isUnresolved && (
              <InlineResolve toolName={r.name} onPick={(choice) => setToolResolution(r.name, choice)} />
            )}
          />
        ))}
      </MapGroup>

      <MapGroup
        label={MAP_COPY.groups.rules.label}
        hint={MAP_COPY.groups.rules.hint}
        right={MAP_COPY.rulesRight(ruleRows.length)}
      >
        {ruleRows.map((r, i) => (
          <MapRow key={r.key} index={i} name={r.name} origin={r.origin} target={r.target} mono />
        ))}
      </MapGroup>

      <MapGroup
        label={MAP_COPY.groups.stores.label}
        hint={MAP_COPY.groups.stores.hint}
        right={MAP_COPY.storesRight(storeRows.length)}
      >
        {storeRows.map((r, i) => (
          <MapRow key={r.key} index={i} name={r.name} origin={r.origin} target={r.target} mono />
        ))}
        <MapRow index={storeRows.length} name={null} origin={null} target={MAP_COPY.derivedStore} derived />
      </MapGroup>

      <MapGroup
        label={MAP_COPY.groups.actors.label}
        hint={MAP_COPY.groups.actors.hint}
        right={MAP_COPY.actorsRight(actorMapped, actors.length - actorMapped)}
      >
        {actors.map((a, i) => (
          <MapRow
            key={a.key}
            index={i}
            name={a.name}
            origin={a.origin}
            target={a.target}
            mono={!!a.name}
            derived={!a.mapped}
          />
        ))}
      </MapGroup>
    </Box>
  );
}
SourceToSandboxMap.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE,
  patch: PropTypes.func,
};

function ColHead({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3", fontWeight: "fontWeightBold",
        textTransform: "uppercase", letterSpacing: 0.6, color: "text.subtitle",
      }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node };

function MapGroup({ label, hint, right, toAnswer, children, first }) {
  return (
    <Box>
      <Box
        sx={{
          display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", columnGap: 2,
          alignItems: "baseline", px: MAP_PX, pt: first ? 2 : 2.25, pb: 1,
          borderTop: first ? "none" : "1px solid", borderColor: "divider",
        }}
      >
        <Stack direction="row" alignItems="baseline" spacing={1} sx={{ minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{label}</Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{hint}</Typography>
        </Stack>
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", color: toAnswer ? "primary.main" : "text.subtitle" }}>
          {right}
        </Typography>
      </Box>
      <Box sx={{ pb: 1 }}>{children}</Box>
    </Box>
  );
}
MapGroup.propTypes = {
  label: PropTypes.string, hint: PropTypes.string, right: PropTypes.string,
  toAnswer: PropTypes.number, children: PropTypes.node, first: PropTypes.bool,
};

function MapRow({ index, name, origin, target, mono, highlight, derived, trailing, below }) {
  const rowBg = (t) => {
    if (highlight) return alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.06 : 0.03);
    if (index % 2 === 1) return alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.025 : 0.015);
    return "transparent";
  };
  return (
    <Box sx={{ position: "relative", bgcolor: rowBg }}>
      <Box
        sx={{
          display: "grid", gridTemplateColumns: MAP_GRID, columnGap: 2.5,
          alignItems: "center", minHeight: highlight ? 44 : 40, pl: MAP_PX, pr: MAP_PX,
        }}
      >
        {name ? (
          <Typography
            noWrap
            sx={{
              typography: "s2", fontWeight: "fontWeightSemiBold",
              fontFamily: mono ? MONO : undefined,
              color: derived ? "text.subtitle" : "text.primary",
            }}
          >
            {name}
          </Typography>
        ) : <Box />}

        <Box sx={{ justifySelf: "end", display: "flex" }}>
          {origin && <OriginChip origin={origin} showPath={false} />}
        </Box>

        <Iconify
          icon="solar:arrow-right-linear"
          width={14}
          sx={{ color: derived ? "text.disabled" : "text.subtitle", justifySelf: "center" }}
        />

        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
          {/* String targets get the row's own text styling; a node target (the
              "Needs your answer" marker) styles itself, so it renders as-is
              rather than nested inside a <p>. */}
          {typeof target === "string" && (
            <Typography
              noWrap
              sx={{
                typography: "s2",
                color: derived ? "text.subtitle" : "text.primary",
                fontStyle: derived ? "italic" : "normal",
                minWidth: 0,
              }}
            >
              {target}
            </Typography>
          )}
          {typeof target !== "string" && target}
          {trailing}
        </Stack>
      </Box>

      {below && <Box sx={{ pl: MAP_PX, pr: MAP_PX, pb: 2, pt: 0.25 }}>{below}</Box>}
    </Box>
  );
}
MapRow.propTypes = {
  index: PropTypes.number, name: PropTypes.node, origin: PropTypes.string,
  target: PropTypes.node, mono: PropTypes.bool, highlight: PropTypes.bool,
  derived: PropTypes.bool, trailing: PropTypes.node, below: PropTypes.node,
};
