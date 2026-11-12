// Section 3 · What this run uses (R4-SPEC D7; lane Ra MUST 5): Models ·
// Tools (side-effect icon) · MCP servers · Prompts · Parameters · Code.
// Values come from the handshake (declared) and earlier runs (seen); a
// declared-but-unseen item gets an amber dot. Parameters render as
// controls; Code offers a diff snapshot.
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  FormControlLabel,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CopyId from "src/sections/common/ux/CopyId";
import { HoverCard } from "src/sections/common/ux/oneview";
import Term from "src/sections/common/ux/Term";
import ParamControls from "./ParamControls";
import { Dot } from "./parts";

const DOT = {
  both: {
    color: "success.main",
    title: "Declared by the agent and seen in a run",
  },
  declared: {
    color: "warning.main",
    title: "Declared but not seen in a run yet",
  },
  seen: {
    color: "info.main",
    title: "Seen in a run, not declared by the agent",
  },
};
const dotOf = (x) =>
  x.declared && x.seen ? DOT.both : x.declared ? DOT.declared : DOT.seen;

function Chip({ item, icon }) {
  const d = dotOf(item);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{
        px: 0.75,
        py: 0.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        maxWidth: "100%",
      }}
    >
      <Dot color={d.color} title={d.title} />
      <Typography
        typography="s2"
        noWrap
        sx={{ fontFamily: icon ? undefined : "inherit" }}
      >
        {item.name}
      </Typography>
      {item.side_effect ? (
        <CustomTooltip
          show
          title="Side effect: changes something outside the agent (refunds, orders)"
          size="small"
          placement="top"
        >
          <Box component="span" sx={{ display: "inline-flex" }}>
            <Iconify
              icon="mdi:flash-alert-outline"
              width={14}
              sx={{ color: "warning.dark" }}
              aria-label="side effect"
            />
          </Box>
        </CustomTooltip>
      ) : null}
    </Stack>
  );
}
Chip.propTypes = { item: PropTypes.object.isRequired, icon: PropTypes.string };

function Row({ label, children }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "110px minmax(0, 1fr)",
        gap: 1.5,
        alignItems: "start",
      }}
    >
      <Typography typography="s2" color="text.secondary" sx={{ pt: 0.25 }}>
        {label}
      </Typography>
      <Box sx={{ minWidth: 0 }}>{children}</Box>
    </Box>
  );
}
Row.propTypes = { label: PropTypes.node, children: PropTypes.node };

const Chips = ({ items }) =>
  items?.length ? (
    <Stack direction="row" flexWrap="wrap" gap={0.75}>
      {items.map((x) => (
        <Chip key={x.name} item={x} />
      ))}
    </Stack>
  ) : (
    <Typography typography="s2" color="text.disabled">
      None declared or seen yet
    </Typography>
  );
Chips.propTypes = { items: PropTypes.array };

/** The legend hover card for the dots. */
export function DotsLegend() {
  return (
    <HoverCard
      trigger={
        <Typography
          component="span"
          typography="s3"
          color="text.secondary"
          sx={{ textDecoration: "underline dotted" }}
        >
          What the dots mean
        </Typography>
      }
      title="Declared and seen"
    >
      <Stack spacing={0.75} sx={{ p: 0.5 }}>
        {Object.values(DOT).map((d) => (
          <Stack key={d.title} direction="row" gap={1} alignItems="center">
            <Dot color={d.color} title={d.title} />
            <Typography typography="s2">{d.title}</Typography>
          </Stack>
        ))}
        <Typography typography="s3" color="text.secondary">
          Declared = what the agent says it uses when it connects. Seen = what
          the spans of a run show.
        </Typography>
      </Stack>
    </HoverCard>
  );
}

export default function WhatItUses({
  uses,
  values,
  onParams,
  diffSnapshot,
  onDiffSnapshot,
  sourceNote,
}) {
  const u = uses ?? {};
  const empty = !u.models?.length && !u.tools?.length && !u.params?.length;
  if (empty)
    return (
      <Typography
        typography="s2"
        color="text.secondary"
        data-testid="uses-empty"
      >
        Filled from your agent: when it connects (Connect live), or from the
        spans of your first traced row.
      </Typography>
    );
  const code = u.code;
  const files = code?.files ?? [];
  return (
    <Stack spacing={1.25} data-testid="what-it-uses">
      <Stack direction="row" gap={1} alignItems="center">
        {sourceNote ? (
          <Typography typography="s3" color="text.secondary">
            {sourceNote}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        <DotsLegend />
      </Stack>
      <Row label="Models">
        <Chips items={u.models} />
      </Row>
      <Row label="Tools">
        <Chips items={u.tools} />
      </Row>
      <Row label={<Term k="mcp">MCP servers</Term>}>
        <Chips items={u.mcp} />
      </Row>
      <Row label="Prompts">
        <Chips items={u.prompts} />
      </Row>
      <Row label={<Term k="declaredParam">Parameters</Term>}>
        {u.params?.length ? (
          <ParamControls
            schema={u.params}
            values={values ?? {}}
            onChange={onParams}
          />
        ) : (
          <Typography typography="s2" color="text.disabled">
            None declared
          </Typography>
        )}
      </Row>
      {code ? (
        <Row label="Code">
          <Stack spacing={0.5}>
            <Stack direction="row" alignItems="center" gap={0.75}>
              <Typography typography="s2">git</Typography>
              <CopyId id={code.commit} label="sha" />
              {files.length ? (
                <CustomTooltip
                  show
                  title={files.join(", ")}
                  size="small"
                  placement="top"
                >
                  <Typography typography="s2" color="text.secondary">
                    {`+ ${files.length} changed ${files.length === 1 ? "file" : "files"}`}
                  </Typography>
                </CustomTooltip>
              ) : null}
            </Stack>
            {files.length ? (
              <CustomTooltip
                show
                title="A dirty working tree isn't reproducible from the sha alone"
                size="small"
                placement="top"
              >
                <FormControlLabel
                  control={
                    <Checkbox
                      size="small"
                      checked={Boolean(diffSnapshot)}
                      onChange={(e) => onDiffSnapshot?.(e.target.checked)}
                    />
                  }
                  label={
                    <Typography typography="s2">
                      Save a diff snapshot with the run
                    </Typography>
                  }
                  sx={{ ml: -0.5, width: "fit-content" }}
                />
              </CustomTooltip>
            ) : null}
          </Stack>
        </Row>
      ) : null}
    </Stack>
  );
}

WhatItUses.propTypes = {
  uses: PropTypes.object,
  values: PropTypes.object,
  onParams: PropTypes.func,
  diffSnapshot: PropTypes.bool,
  onDiffSnapshot: PropTypes.func,
  sourceNote: PropTypes.string,
};
