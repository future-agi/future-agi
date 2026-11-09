import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import BundledIcon from "src/sections/common/RunSources/BundledIcon";
import {
  brandedEntryOfConfig,
  sharesMark,
} from "src/sections/common/RunSources/brandLogos";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import { kindLabel, runKindOf } from "./runKind";
import { runShortName } from "./runLabel";

export const RUN_KIND_ICONS = Object.freeze({
  prompt: "mdi:text-box-outline",
  agent: "mdi:graph-outline",
  code: "mdi:code-braces",
  traces: "mdi:transit-connection-variant",
  harness: "mdi:console",
  voice: "mdi:phone-in-talk",
  http: "mdi:api",
  upload: "mdi:upload-outline",
});

const join = (parts) => parts.filter(Boolean).join(" · ");

/**
 * One line describing a run's setup, from its config:
 * `the coding harness Code × claude-sonnet-4-5 · Docker · 30 turns max` ·
 * `LangGraph support agent · alice@mbp-14 · git 3fa91c2` ·
 * `Support Reply V5 × gpt-4o-mini · 6 tools (mock)`.
 */
export function runSourceLine(run) {
  const c = run?.config ?? {};
  const kind = runKindOf(run);
  switch (kind) {
    case "harness": {
      const h = c.harness ?? {};
      return join([
        [h.name, h.model].filter(Boolean).join(" × "),
        h.sandbox ? String(h.sandbox).split(" · ")[0] : null,
        h.max_turns ? `${h.max_turns} turns max` : null,
      ]);
    }
    case "code": {
      const code = c.code ?? {};
      return join([
        run?.name ?? code.repo,
        code.host,
        code.commit ? `git ${code.commit}` : null,
      ]);
    }
    case "prompt": {
      const tools = c.tools ?? [];
      const modes = [...new Set(tools.map((t) => t.mode).filter(Boolean))];
      return join([
        [
          [c.prompt?.name, c.prompt?.version].filter(Boolean).join(" "),
          c.model?.id,
        ]
          .filter(Boolean)
          .join(" × "),
        tools.length
          ? `${tools.length} ${tools.length === 1 ? "tool" : "tools"}${modes.length === 1 ? ` (${modes[0]})` : ""}`
          : null,
      ]);
    }
    case "agent": {
      const a = c.agent ?? {};
      return join([
        [a.name, a.version].filter(Boolean).join(" "),
        a.status === "draft" ? "draft" : null,
      ]);
    }
    case "traces": {
      const t = c.traces ?? {};
      return join([t.project, t.version]);
    }
    case "voice": {
      const v = c.voice ?? {};
      return join([
        run?.name,
        v.depth === "text_only" ? "Text only" : null,
        v.stt?.model,
        v.llm?.model,
      ]);
    }
    case "http":
      return join([run?.name, "Future AGI calls your endpoint"]);
    case "upload":
      return join([run?.name, "Uploaded results"]);
    default:
      return run?.name ?? "";
  }
}

/** True when the run's own name already names the brand (`the coding harness Code 2.x`). */
const nameSaysBrand = (run, entry) => {
  const name = runShortName(run).toLowerCase();
  return [entry.name, entry.short].some(
    (w) => w && name.includes(w.toLowerCase()),
  );
};

/** True when the run says what kind it is (config or source). */
export const hasSourceInfo = (run) =>
  Boolean(run?.config?.kind || run?.source?.kind || run?.kind);

/**
 * The run's kind as an icon + short word (`Agent`, `Prompt + tools`,
 * `Harness`), with a one-line tooltip from its config. A run whose config
 * names a brand (the coding harness Code, LangGraph, LiveKit) shows that brand's mark
 * alone at 16 px (the word moves to the tooltip), except a mark two brands
 * share (the coding harness Code, the coding harness Agent SDK: the the coding harness spark), which keeps the
 * brand's name next to it so the two runs differ without hovering, unless
 * the run's own name already says the brand (`the coding harness Code 2.x (headless)`):
 * that name sits beside the badge in a chip, a banner or the Test a fix
 * dock, so the word would only repeat it. Every other run keeps its kind's
 * one icon + word (an endpoint is always `mdi:api`, as on the catalog's
 * HTTP tile; a custom command always `mdi:console`), all bundled, never
 * fetched. Renders nothing for runs without a config / source (round-1 runs
 * look as before).
 *
 * Props (frozen, R2 manifest row 43):
 * @param {object} props.run  canonical Run with `config` (R1 include=config)
 * @param {"small"|"medium"} [props.size="small"]
 * @param {boolean} [props.nameShown=true]  additive (L5 G3): the run's name
 *   is rendered beside the badge; when false (a chip with its name hidden)
 *   a shared mark always keeps its brand word (`[spark] the coding harness Code`)
 */
const RunSourceBadge = ({ run, size = "small", nameShown = true }) => {
  if (!hasSourceInfo(run)) return null;
  const kind = runKindOf(run);
  const word = kindLabel(kind, { tools: run?.config?.tools });
  const line = runSourceLine(run) || word;
  const font = size === "medium" ? "s2" : "s3";
  const iconPx = size === "medium" ? 16 : 14;
  // A brand's mark says more than the kind word, so a branded source shows
  // the mark alone (the word moves to the tooltip), never below 16 px;
  // generic kinds (prompt, agent graph, endpoint, upload, custom command)
  // keep their kind icon + word, so one kind always shows one icon. A mark
  // two brands share keeps the brand's short name beside it, unless the
  // run's own name already says it (no `the coding harness Code the coding harness Code`).
  const entry = brandedEntryOfConfig(run?.config, kind);
  const branded = Boolean(entry);
  const shownWord = branded
    ? sharesMark(entry.id) && !(nameShown && nameSaysBrand(run, entry))
      ? entry.short ?? entry.name
      : null
    : word;
  return (
    <CustomTooltip
      show
      title={branded && line !== word ? `${word} · ${line}` : line}
      placement="top"
      size="small"
      arrow
    >
      <Box
        component="span"
        data-run-kind={kind}
        aria-label={branded ? `${word} · ${entry.name}` : word}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.375,
          flexShrink: 0,
          color: "text.secondary",
          whiteSpace: "nowrap",
        }}
      >
        {branded ? (
          <SourceLogo entry={entry} size={16} />
        ) : (
          <BundledIcon
            icon={RUN_KIND_ICONS[kind] ?? RUN_KIND_ICONS.prompt}
            width={iconPx}
            sx={{ flexShrink: 0 }}
          />
        )}
        {shownWord ? (
          <Typography component="span" typography={font} color="inherit" noWrap>
            {shownWord}
          </Typography>
        ) : null}
      </Box>
    </CustomTooltip>
  );
};

RunSourceBadge.propTypes = {
  run: PropTypes.object,
  size: PropTypes.oneOf(["small", "medium"]),
  nameShown: PropTypes.bool,
};

export default RunSourceBadge;
