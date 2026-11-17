/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { KIND_LABEL, hubPaths } from "../constants";
import { useHubVersion } from "../api/queries";
import { countIfAny, splitRef } from "../lib/format";
import { showsInstalls, useInstallPath } from "../lib/installs";
import { anyOneKey } from "../lib/keys";
import { cliCommands } from "../lib/useThis";

import CopyCommand, { CopyButton } from "./CopyCommand";
import { EntryAction, hasEntryAction } from "./EntryCommands";
import EntryCover from "./EntryCover";
import { hubPageQuery } from "./HubFrame";
import HubIcon from "./HubIcon";
import TierBadge from "./TierBadge";
import { toneSx } from "./ToneChip";

const MONO_FONT =
  "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";

// Text kept to whole lines: at most `lines` of it, the last one ending in an
// ellipsis when there is more. The clamp sits on the one element that holds
// the text, and the card neither stretches nor squeezes that element (a
// card's spare height goes to the space above its metadata, or under a
// pick's demo), so no part of a line, and no line after the ellipsis, ever
// shows.
const clampSx = (lines) => ({
  display: "-webkit-box",
  WebkitLineClamp: lines,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
  flex: "none",
});

// How many lines each text of a card shows at most: the title, the tagline
// and What it does. A maintainer's why is never cut: the server caps it.
const LINES = { title: 2, tagline: 2, about: 4 };

// Columns by the Hub page's own width (HubFrame is the container): 4 from
// 1120 px (a 1440 window with the sidebar, about 1150 px, still 4 with a
// classic scrollbar), 3 from 880 px (1280), 2 from 560 px (1024 with the
// sidebar), 1 below (a phone). A grid of 3 stops at 3. The window
// breakpoints first are for a browser without container queries.
const LIST_SX = {
  listStyle: "none",
  m: 0,
  p: 0,
  minWidth: 0,
  display: "grid",
  gap: 2,
};

const GRID_3_SX = {
  ...LIST_SX,
  gridTemplateColumns: {
    xs: "minmax(0, 1fr)",
    sm: "repeat(2, minmax(0, 1fr))",
    lg: "repeat(3, minmax(0, 1fr))",
  },
  [hubPageQuery("(max-width: 559.98px)")]: {
    gridTemplateColumns: "minmax(0, 1fr)",
  },
  [hubPageQuery("(min-width: 560px)")]: {
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  },
  [hubPageQuery("(min-width: 880px)")]: {
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  },
};

const GRID_SX = {
  3: GRID_3_SX,
  4: {
    ...GRID_3_SX,
    [hubPageQuery("(min-width: 1120px)")]: {
      gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
    },
  },
};

// A grid whose rows are always full (Start here): 12 tracks, and each card
// spans its share of its row at each of the widths above (fillSpans).
const FILL_GRID_SX = {
  ...LIST_SX,
  gridTemplateColumns: "repeat(12, minmax(0, 1fr))",
};

// Every card in a row has the row's height, so the feet (and their
// actions) line up at the bottom.
const ITEM_SX = { minWidth: 0, display: "flex", flexDirection: "column" };

/**
 * How `count` cards fill rows of at most `columns`, as each card's span on a
 * 12-track grid, row by row. Every row is full (its spans add up to 12), and
 * the rows with fewer, wider cards come first: 3 cards at 2 columns are one
 * wide card, then two halves.
 */
export function fillSpans(count, columns) {
  const rows = Math.ceil(count / columns);
  const base = Math.floor(count / rows);
  const wider = rows - (count % rows);
  const spans = [];
  for (let row = 0; row < rows; row += 1) {
    const size = row < wider ? base : base + 1;
    spans.push(...Array(size).fill(12 / size));
  }
  return spans;
}

/**
 * The column counts (2 up to `columns`) at which `count` cards leave the
 * last row short.
 */
export function lastRowShortAt(count, columns = 4) {
  const shortAt = [];
  for (let most = 2; most <= columns; most += 1) {
    if (count % most) shortAt.push(most);
  }
  return shortAt;
}

const span = (tracks) => `span ${tracks}`;

// One card of a filled grid: `spans` is its span at 1, 2, 3 and 4 columns.
// It takes its row's height at every width, as every card does.
function fillItemSx([one, two, three, four]) {
  return {
    ...ITEM_SX,
    gridColumn: { xs: span(one), sm: span(two), lg: span(three) },
    [hubPageQuery("(max-width: 559.98px)")]: { gridColumn: span(one) },
    [hubPageQuery("(min-width: 560px)")]: { gridColumn: span(two) },
    [hubPageQuery("(min-width: 880px)")]: { gridColumn: span(three) },
    [hubPageQuery("(min-width: 1120px)")]: { gridColumn: span(four) },
  };
}

// The cell after the last card (`end`): shown only at the widths where the
// last row of `count` cards is short, so it fills one empty slot and never
// starts a row of its own, and it is gone wherever that row is full.
function endSx(count, columns) {
  const at = (most) => (count % Math.min(most, columns) ? "flex" : "none");
  return {
    ...ITEM_SX,
    display: { xs: "none", sm: at(2), lg: at(3) },
    [hubPageQuery("(max-width: 559.98px)")]: { display: "none" },
    [hubPageQuery("(min-width: 560px)")]: { display: at(2) },
    [hubPageQuery("(min-width: 880px)")]: { display: at(3) },
    [hubPageQuery("(min-width: 1120px)")]: { display: at(4) },
  };
}

const CARD_SX = {
  position: "relative",
  flex: 1,
  minWidth: 0,
  overflowWrap: "anywhere",
  display: "flex",
  flexDirection: "column",
  gap: 1.25,
  p: 2,
  borderRadius: 1,
  border: "1px solid",
  borderColor: "divider",
  bgcolor: "background.paper",
  transition: "border-color 120ms",
  "&:hover, &:focus-within": { borderColor: "border.hover" },
};

// The title link covers the whole card (its ::after), so a click anywhere on
// the card opens the entry; the controls on the card, and the tier and the
// counts (for their tooltips), sit above it. Focus draws one ring around the
// card, not around the title text.
const STRETCHED_LINK_SX = {
  "&::after": {
    content: '""',
    position: "absolute",
    inset: 0,
    borderRadius: 1,
  },
  "&:focus-visible": { outline: "none" },
  "&:focus-visible::after": {
    outline: "2px solid",
    outlineColor: (theme) => theme.palette.primary.main,
    outlineOffset: 2,
  },
};

const ABOVE_LINK = { position: "relative", zIndex: 1 };

// A reference on one line, never broken inside a name: a long one ends in an
// ellipsis, and its title (on hover) gives it whole. It sits above the
// card's link, so the hover reaches it.
const ONE_LINE_SX = { ...ABOVE_LINK, minWidth: 0 };

// The card's own reference, in mono on one line. It takes keyboard focus
// (with a ring), so its whole text is reachable without a pointer.
const REF_SX = {
  ...ONE_LINE_SX,
  fontFamily: MONO_FONT,
  borderRadius: 0.5,
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: (theme) => theme.palette.primary.main,
    outlineOffset: 2,
  },
};

function MetaDot() {
  return (
    <Box component="span" aria-hidden="true" sx={{ typography: "s2" }}>
      ·
    </Box>
  );
}

const NO_KEY = "Runs without a key";

const HIDDEN_TEXT = {
  position: "absolute",
  width: "1px",
  height: "1px",
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};

function Meta({ icon, value, label }) {
  return (
    <Tooltip title={label}>
      <Box
        component="span"
        aria-label={`${label}: ${value}`}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          color: "text.secondary",
        }}
      >
        <HubIcon name={icon} size={13} />
        <Typography component="span" typography="s2">
          {value}
        </Typography>
      </Box>
    </Tooltip>
  );
}

Meta.propTypes = {
  icon: PropTypes.string,
  value: PropTypes.node,
  label: PropTypes.string,
};

// The copy button beside the reference is a 25 px target (WCAG 2.2 AA asks
// for 24), pulled back by its own margin so the reference keeps its line
// height.
const COPY_REF_SX = {
  ...ABOVE_LINK,
  alignSelf: "flex-start",
  p: 0.75,
  my: -0.5,
  mr: -0.5,
  color: "text.secondary",
};

const MAX_DEMO_LINES = 4;

// The lines GET /picks gives a pick from its latest version's recorded demo
// (`demoExcerpt`: plain strings, or null); none when it has none.
const demoLines = (excerpt) =>
  Array.isArray(excerpt)
    ? excerpt
        .filter((line) => typeof line === "string" && line.trim())
        .slice(0, MAX_DEMO_LINES)
    : [];

/** Whether a pick has lines from its recorded demo to show. */
export const hasDemo = (entry) => demoLines(entry?.demoExcerpt).length > 0;

// The excerpt takes the card's spare height under its code box, so the feet
// of a row line up; the box itself is as tall as its lines (DEMO_LINES_SX),
// so it never stretches into an empty box beside a taller pick.
const DEMO_SX = {
  m: 0,
  flex: 1,
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  gap: 0.75,
};

const DEMO_LINES_SX = {
  flex: "none",
  m: 0,
  px: 1.25,
  py: 1,
  minWidth: 0,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 0.75,
  bgcolor: "background.neutral",
  fontFamily: MONO_FONT,
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
};

// "From its recorded demo": what the entry printed when its demo was
// recorded, each line as it came and as text only (no link, markup or
// colour is added), in one quiet mono block. A long line wraps.
function DemoExcerpt({ lines }) {
  return (
    <Box component="figure" data-testid="hub-entry-card-demo" sx={DEMO_SX}>
      <Typography component="figcaption" typography="s2" color="text.secondary">
        From its recorded demo
      </Typography>
      <Typography
        component="pre"
        typography="s1"
        color="text.secondary"
        sx={DEMO_LINES_SX}
      >
        {lines.map((line, index) => (
          <Box
            key={index}
            component="span"
            data-testid="hub-entry-card-demo-line"
            sx={{ display: "block" }}
          >
            {line}
          </Box>
        ))}
      </Typography>
    </Box>
  );
}

DemoExcerpt.propTypes = {
  lines: PropTypes.arrayOf(PropTypes.string).isRequired,
};

const MAX_TAGS = 5;

// An entry's tags as a card shows them: the first few, each once.
function tagsOf(tags) {
  if (!Array.isArray(tags)) return [];
  const named = tags.filter((tag) => typeof tag === "string" && tag.trim());
  return [...new Set(named)].slice(0, MAX_TAGS);
}

// What it does, the block around its text and tags, and how it runs are as
// tall as what they hold: the card takes its row's height, and its spare
// height goes above its metadata, as on any card, so no block stretches into
// an empty box.
const ABOUT_SX = { ...DEMO_SX, flex: "none" };

const ABOUT_BODY_SX = {
  flex: "none",
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  gap: 1,
  px: 1.25,
  py: 1,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 0.75,
  bgcolor: "background.neutral",
};

const TAGS_SX = {
  listStyle: "none",
  m: 0,
  p: 0,
  minWidth: 0,
  display: "flex",
  flexWrap: "wrap",
  gap: 0.5,
};

// "What it does" on a pick with no recorded demo, in the place and the
// quiet block a demo takes: what the entry does (its description when the
// pick carries one, else its tagline), in whole lines, then its tags as the
// entry page draws them (a long tag ends in an ellipsis).
function WhatItDoes({ text, tags }) {
  if (!text && !tags.length) return null;
  return (
    <Box data-testid="hub-entry-card-about" sx={ABOUT_SX}>
      <Typography typography="s2" color="text.secondary">
        What it does
      </Typography>
      <Box sx={ABOUT_BODY_SX}>
        {text && (
          <Typography
            typography="s1"
            color="text.secondary"
            data-testid="hub-entry-card-about-text"
            sx={clampSx(LINES.about)}
          >
            {text}
          </Typography>
        )}
        {tags.length > 0 && (
          <Box
            component="ul"
            role="list"
            aria-label="Tags"
            data-testid="hub-entry-card-tags"
            sx={TAGS_SX}
          >
            {tags.map((tag) => (
              <Chip
                component="li"
                key={tag}
                label={tag}
                size="small"
                variant="outlined"
                sx={{ maxWidth: "100%" }}
              />
            ))}
          </Box>
        )}
      </Box>
    </Box>
  );
}

WhatItDoes.propTypes = {
  text: PropTypes.string,
  tags: PropTypes.arrayOf(PropTypes.string).isRequired,
};

// A word of a command as a shell reads it: one with a space or a character
// the shell treats specially is single-quoted, so the command shown runs
// the exact words the manifest lists.
const PLAIN_WORD = /^[\w@%+=:,./-]+$/;
const shellWord = (word) =>
  PLAIN_WORD.test(word) ? word : `'${word.replace(/'/g, "'\\''")}'`;

// The manifest's run.keyless (the words of the command, in order) as one
// command, or null when it lists none.
function runCommandOf(keyless) {
  if (!Array.isArray(keyless) || !keyless.length) return null;
  if (!keyless.every((word) => typeof word === "string")) return null;
  return keyless.map(shellWord).join(" ");
}

// The names of the keys a live run reads (the manifest's keys[]), each once.
function keyNamesOf(keys) {
  if (!Array.isArray(keys)) return [];
  const names = keys
    .map((key) => key?.name)
    .filter((name) => typeof name === "string" && name);
  return [...new Set(names)];
}

const KEY_NAME_SX = {
  fontFamily: MONO_FONT,
  color: "text.primary",
  whiteSpace: "nowrap",
};

// The caption over a pick's run command. The command line's run command
// runs anywhere. The manifest's runs inside the entry's files, so its
// caption says where; it adds "without a key" only where the card's foot
// does not already say so.
function runCaption(fromCli, noKeyNeeded) {
  if (fromCli) return "Run it";
  return noKeyNeeded
    ? "In its files, run it"
    : "In its files, run it without a key";
}

// How a pick with no recorded demo runs, under What it does: one command to
// run it, with copy, then the keys a live run reads (keys[]). Once the
// command line is released, the command is the one the card's Use offers
// (`omega hub run`), so the card shows one way to run it; until then it is
// the manifest's run.keyless. The keys line says "any one of" where one key
// is enough, by the rule the entry page's Keys panel follows (lib/keys.js).
// The manifest comes from the version the pick names, which the picks list
// does not carry, so the card reads that version (the public read the entry
// page makes for a pinned version; it counts no install). While it loads, a
// placeholder of the same shape holds the place; a version that does not
// load, or that names no command and no keys, draws nothing.
function HowItRuns({ entry, title }) {
  const released = useOmegaPart(OMEGA_PART.HUB_CLI);
  const version = useHubVersion(
    entry.publisher?.handle,
    entry.name,
    entry.latestVersion,
  );
  if (version.isLoading) {
    return (
      <Box
        aria-hidden="true"
        data-testid="hub-entry-card-run-loading"
        sx={ABOUT_SX}
      >
        <Skeleton variant="text" width={120} sx={{ fontSize: 12 }} />
        <Skeleton variant="rounded" height={34} />
      </Box>
    );
  }
  if (!version.data) return null;
  const manifest = version.data.manifest;
  const fromCli = released && Boolean(entry.ref);
  const command = fromCli
    ? cliCommands(entry.ref, entry.latestVersion).run
    : runCommandOf(manifest?.run?.keyless);
  const keys = keyNamesOf(manifest?.keys);
  if (!command && !keys.length) return null;
  const anyOne = anyOneKey(
    keys.map((name) => ({ name })),
    manifest?.models,
  );
  const keysLead = anyOne
    ? "For a live run, any one of: "
    : "For a live run, it reads: ";
  return (
    <Box data-testid="hub-entry-card-run" sx={ABOUT_SX}>
      {command && (
        <>
          <Typography typography="s2" color="text.secondary">
            {runCaption(fromCli, entry.noKeyNeeded)}
          </Typography>
          <Box sx={ABOVE_LINK}>
            <CopyCommand
              text={command}
              label={`Copy the run command of ${title}`}
              testId="hub-entry-card-run-command"
            />
          </Box>
        </>
      )}
      {keys.length > 0 && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="hub-entry-card-run-keys"
        >
          {keysLead}
          {keys.map((name, index) => (
            <React.Fragment key={name}>
              {index > 0 && ", "}
              <Box component="code" sx={KEY_NAME_SX}>
                {name}
              </Box>
            </React.Fragment>
          ))}
        </Typography>
      )}
    </Box>
  );
}

HowItRuns.propTypes = {
  entry: PropTypes.shape({
    ref: PropTypes.string,
    name: PropTypes.string,
    latestVersion: PropTypes.string,
    noKeyNeeded: PropTypes.bool,
    publisher: PropTypes.object,
  }).isRequired,
  title: PropTypes.string,
};

// A word and the number a hyphen joins to it ("kill -9", "GPT-4o"): a title
// keeps each such run on one line, so no line of it starts with "-9" and
// none splits "GPT-" from "4o". A run longer than JOINED_MAX is left free to
// wrap, so a long word still breaks inside the card.
const JOINED = /\S+(?: -|-)\d[\w.]*/g;
const JOINED_MAX = 24;
const JOINED_SX = { whiteSpace: "nowrap" };

// A card's title as written, with each joined run above in a span of its own
// that does not wrap. Its text, and so the link's name, is the title exactly.
function TitleText({ text }) {
  const title = String(text ?? "");
  const parts = [];
  let from = 0;
  for (const run of title.matchAll(JOINED)) {
    if (run[0].length <= JOINED_MAX) {
      parts.push(title.slice(from, run.index));
      parts.push(
        <Box key={run.index} component="span" sx={JOINED_SX}>
          {run[0]}
        </Box>,
      );
      from = run.index + run[0].length;
    }
  }
  parts.push(title.slice(from));
  return parts.filter((part) => part !== "");
}

TitleText.propTypes = { text: PropTypes.string };

// The title: at most two whole lines, evened out where the browser can
// (text-wrap: pretty) so its last word does not sit alone on a line; a
// browser without it wraps as before.
const TITLE_SX = { m: 0, textWrap: "pretty", ...clampSx(LINES.title) };

// One entry in a grid. The cover tile sits beside the title, which is the
// link (a heading, so a screen reader can move from card to card), with the
// publisher/name reference in mono under it on one line (a long one ends in
// an ellipsis; its tooltip and its copy button give it whole). Then what it
// does, in whole lines, and kind, tier and the latest version as quiet
// metadata; a card taller than its text (its row holds a longer one) keeps
// the extra height above the metadata. The foot says "Runs without a key"
// when it does and holds the card's action (EntryAction: Use with both
// commands once they can run, else Get the files). A count shows only once
// there is one: a new Hub shows no "♡ 0" or "⬇ —". Installs only where they
// can be counted (lib/installs.js). `variant="pick"` is the Start here card:
// the maintainer's why leads in primary text, then, when the pick has a
// recorded demo, the tagline and what the demo printed, else What it does
// (the description, or the tagline) with the tags, and how it runs (its run
// command and the keys a live run reads), in the demo's place.
export default function EntryCard({ entry, variant = "default" }) {
  const installPath = useInstallPath();
  const href = hubPaths.entry(entry.publisher?.handle, entry.name);
  const derived = entry.remixOf ? splitRef(entry.remixOf) : null;
  const derivedFrom = derived
    ? `Derived from ${derived.publisher}/${derived.name}`
    : null;
  const likeCount = countIfAny(entry.likes);
  const installCount = showsInstalls(installPath, entry.installs30d)
    ? countIfAny(entry.installs30d)
    : null;
  const title = entry.title || entry.name;
  const why = entry.pick?.why;
  const pickCard = variant === "pick" && Boolean(why);
  const demo = variant === "pick" ? demoLines(entry.demoExcerpt) : [];
  // A pick with no recorded demo says what it does in the demo's place.
  const about = variant === "pick" && !demo.length;
  const actionable = hasEntryAction(entry);
  return (
    <Box
      component="article"
      data-testid="hub-entry-card"
      data-ref={entry.ref}
      sx={CARD_SX}
    >
      <Box
        sx={{
          display: "flex",
          gap: variant === "pick" ? 1.5 : 1.25,
          alignItems: "flex-start",
          minWidth: 0,
        }}
      >
        <EntryCover cover={entry.cover} size={variant === "pick" ? 40 : 32} />
        <Box
          sx={{
            minWidth: 0,
            flex: 1,
            display: "flex",
            flexDirection: "column",
            gap: 0.25,
          }}
        >
          <Typography
            component="h3"
            typography="m3"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
            data-testid="hub-entry-card-title"
            sx={TITLE_SX}
          >
            <Link
              component={RouterLink}
              href={href}
              color="inherit"
              underline="hover"
              sx={STRETCHED_LINK_SX}
            >
              <TitleText text={title} />
            </Link>
          </Typography>
          <Box
            sx={{
              display: "flex",
              alignItems: "flex-start",
              gap: 0.25,
              minWidth: 0,
            }}
          >
            {/* The whole reference is the element's name, its title while
                the tooltip is closed, and the tooltip on hover and on
                keyboard focus. */}
            <Tooltip title={entry.ref} describeChild>
              <Typography
                component="span"
                typography="s2"
                color="text.secondary"
                noWrap
                tabIndex={0}
                aria-label={entry.ref}
                data-testid="hub-entry-card-ref"
                sx={REF_SX}
              >
                {entry.ref}
              </Typography>
            </Tooltip>
            <CopyButton
              text={entry.ref}
              label={`Copy reference ${entry.ref}`}
              tooltip="Copy reference"
              size={13}
              testId="hub-entry-card-copy-ref"
              sx={COPY_REF_SX}
            />
          </Box>
        </Box>
      </Box>
      {pickCard && (
        <Typography
          typography="s1_2"
          color="text.primary"
          data-testid="hub-entry-card-why"
          sx={{ flex: "none" }}
        >
          {why}
        </Typography>
      )}
      {about ? (
        <>
          <WhatItDoes
            text={entry.description || entry.tagline}
            tags={tagsOf(entry.tags)}
          />
          <HowItRuns entry={entry} title={title} />
        </>
      ) : (
        <Typography
          typography="s1"
          color="text.secondary"
          data-testid="hub-entry-card-tagline"
          sx={clampSx(LINES.tagline)}
        >
          {entry.tagline}
        </Typography>
      )}
      {demo.length > 0 && <DemoExcerpt lines={demo} />}
      {why && !pickCard && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="hub-entry-card-why"
          sx={{ flex: "none" }}
        >
          {`Maintainer pick: ${why}`}
        </Typography>
      )}
      {derivedFrom && (
        <Typography
          typography="s3"
          color="text.tertiary"
          noWrap
          title={derivedFrom}
          sx={ONE_LINE_SX}
        >
          {derivedFrom}
        </Typography>
      )}
      {entry.ranked === false && (
        <Typography typography="s3" color="text.tertiary">
          Not ranked
        </Typography>
      )}
      <Box
        data-testid="hub-entry-card-meta"
        sx={{
          alignSelf: "stretch",
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          minWidth: 0,
          mt: "auto",
          color: "text.secondary",
        }}
      >
        <Typography
          component="span"
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ flexShrink: 0 }}
        >
          {KIND_LABEL[entry.kind] || entry.kind}
        </Typography>
        <MetaDot />
        <Box sx={{ ...ABOVE_LINK, flexShrink: 0, display: "inline-flex" }}>
          <TierBadge tier={entry.publisher?.tier} short quiet />
        </Box>
        {entry.latestVersion && (
          <>
            <MetaDot />
            <Typography
              component="span"
              typography="s2"
              color="text.secondary"
              noWrap
              data-testid="hub-entry-card-version"
              sx={{ fontFamily: MONO_FONT, minWidth: 0 }}
            >
              {`v${entry.latestVersion}`}
            </Typography>
          </>
        )}
        <Box
          sx={{
            ...ABOVE_LINK,
            ml: "auto",
            display: "flex",
            alignItems: "center",
            gap: 1.25,
            flexShrink: 0,
          }}
        >
          {likeCount && <Meta icon="heart" value={likeCount} label="Likes" />}
          {installCount && (
            <Meta
              icon="download"
              value={installCount}
              label="Installs, last 30 days"
            />
          )}
        </Box>
      </Box>
      {(entry.noKeyNeeded || actionable) && (
        <Box
          data-testid="hub-entry-card-foot"
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            minHeight: 30,
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {entry.noKeyNeeded && (
            <Typography
              component="span"
              typography="s2"
              color="text.secondary"
              sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
            >
              <Box
                component="span"
                sx={{ display: "inline-flex", ...toneSx("success") }}
              >
                <HubIcon name="check" size={13} />
              </Box>
              {NO_KEY}
            </Typography>
          )}
          {actionable && (
            <Box sx={{ ml: "auto", display: "inline-flex" }}>
              <EntryAction entry={entry} />
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}

EntryCard.propTypes = {
  entry: PropTypes.shape({
    ref: PropTypes.string,
    name: PropTypes.string,
    title: PropTypes.string,
    tagline: PropTypes.string,
    description: PropTypes.string,
    tags: PropTypes.arrayOf(PropTypes.string),
    kind: PropTypes.string,
    cover: PropTypes.object,
    publisher: PropTypes.object,
    latestVersion: PropTypes.string,
    likes: PropTypes.number,
    installs30d: PropTypes.number,
    noKeyNeeded: PropTypes.bool,
    pick: PropTypes.object,
    demoExcerpt: PropTypes.arrayOf(PropTypes.string),
    remixOf: PropTypes.string,
    ranked: PropTypes.bool,
  }).isRequired,
  variant: PropTypes.oneOf(["default", "pick"]),
};

function itemSx(layouts, index) {
  return layouts ? fillItemSx(layouts.map((spans) => spans[index])) : ITEM_SX;
}

// A list of entries. Every card in a row has the row's height, so the feet
// (and their actions) line up. `columns` (3 or 4) is the most a row holds.
// `fill` makes every row full instead (fillSpans): one card is one wide
// card, two are halves, three thirds, four quarters where four fit. `end`
// is an action (not an entry, and not counted) drawn in one empty slot of a
// short last row, and not at all where that row is full.
export function EntryGrid({
  entries,
  variant = "default",
  columns = 4,
  fill = false,
  end = null,
  testId = "hub-entry-grid",
}) {
  const count = entries.length;
  const layouts = fill
    ? [1, 2, 3, 4].map((most) => fillSpans(count, most))
    : null;
  const withEnd = Boolean(end) && lastRowShortAt(count, columns).length > 0;
  const gridSx = fill ? FILL_GRID_SX : GRID_SX[columns] || GRID_SX[4];
  return (
    <Box component="ul" role="list" data-testid={testId} sx={gridSx}>
      {entries.map((entry, index) => (
        <Box component="li" key={entry.ref} sx={itemSx(layouts, index)}>
          <EntryCard entry={entry} variant={variant} />
        </Box>
      ))}
      {withEnd && (
        <Box
          component="li"
          data-testid="hub-entry-grid-end"
          sx={endSx(count, columns)}
        >
          {end}
        </Box>
      )}
    </Box>
  );
}

EntryGrid.propTypes = {
  entries: PropTypes.array.isRequired,
  variant: PropTypes.oneOf(["default", "pick"]),
  columns: PropTypes.oneOf([3, 4]),
  fill: PropTypes.bool,
  end: PropTypes.node,
  testId: PropTypes.string,
};

// The grid while it loads: placeholders in the card's own shape (cover,
// title, reference, two lines, foot), never a spinner. `label` is what a
// screen reader hears while it waits. A grid of 3 is Start here, whose
// cards have the larger cover.
export function EntryGridSkeleton({
  count = 8,
  columns = 4,
  label = "Loading entries",
  testId = "hub-loading",
}) {
  const cover = columns === 3 ? 40 : 32;
  return (
    <Box
      data-testid={testId}
      role="status"
      aria-busy="true"
      sx={GRID_SX[columns] || GRID_SX[4]}
    >
      <Box component="span" sx={HIDDEN_TEXT}>
        {label}
      </Box>
      {Array.from({ length: count }, (_, i) => (
        <Box
          key={i}
          data-testid="hub-entry-card-skeleton"
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1.25,
            p: 2,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Box
            sx={{
              display: "flex",
              gap: columns === 3 ? 1.5 : 1.25,
              alignItems: "flex-start",
            }}
          >
            <Skeleton variant="rounded" width={cover} height={cover} />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Skeleton variant="text" width="70%" sx={{ fontSize: 13 }} />
              <Skeleton variant="text" width="50%" sx={{ fontSize: 11 }} />
            </Box>
          </Box>
          <Box>
            <Skeleton variant="text" width="100%" sx={{ fontSize: 12 }} />
            <Skeleton variant="text" width="80%" sx={{ fontSize: 12 }} />
          </Box>
          <Skeleton variant="text" width="40%" sx={{ fontSize: 11 }} />
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              pt: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Skeleton variant="text" width={96} sx={{ fontSize: 11 }} />
            <Skeleton variant="rounded" width={60} height={26} />
          </Box>
        </Box>
      ))}
    </Box>
  );
}

EntryGridSkeleton.propTypes = {
  count: PropTypes.number,
  columns: PropTypes.oneOf([3, 4]),
  label: PropTypes.string,
  testId: PropTypes.string,
};
