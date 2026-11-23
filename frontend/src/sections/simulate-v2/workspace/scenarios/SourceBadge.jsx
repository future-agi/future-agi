import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { sourceLinkOf } from "../../_mock/scenarioProvenance";
import { openBuilderChat } from "../../_mock/builderPromptBus";
import { keywordsOf } from "../../_mock/scenarioKeywords";
import {
  personalDataLine,
  personalDataDetail,
  REPRO_LABEL,
} from "../../_mock/scenarioSources";

/**
 * Where a scenario came from — on every scenario row, and as a labelled chip
 * in the scenario's drawer (`labelled`).
 *
 * A source with somewhere to go back to — the Error Feed issue it was built
 * from, the run whose failure it reproduces, the chat that wrote it — is a
 * small chip with that id ("ISS-4501", "Run 3", "Chat") that goes there.
 * Everything else (added by hand, derived, generated, a dataset) is a quiet
 * one-word chip — "By hand", "Generated" — not linked; an icon alone read
 * as a button (a pen next to a title looks like "edit"). The drawer names
 * it in full. Generated or inferred facts read "Assumption" — labelled as
 * such, not credited to a source.
 */
const SHORT = {
  "Added manually": "By hand",
  "Auto-derived": "Derived",
  "From template": "Template",
  Generated: "Generated",
  "Imported from dataset": "Dataset",
  "Added by a rebuild": "Rebuild",
  "From persona library": "Personas",
  "From production trace": "Production",
  "From an uploaded transcript": "Transcript",
  "From a failed simulation": "Simulation",
  "From chat": "Chat",
};
export default function SourceBadge({
  scenario,
  env,
  envState,
  labelled = false,
}) {
  /* The environment lets an improvement-made scenario link to its improvement. */
  const link = sourceLinkOf(scenario, { env, envState });
  /* A production conversation links back to its trace in Observe. */
  const to = link.to || link.observe?.to || null;
  const linked = !!(to || link.chat);
  /* Where it came from, as keywords (from-production, from-issue, …). */
  const keywords = keywordsOf(scenario, envState);

  const title = (
    <Stack spacing={0.375} sx={{ py: 0.25, maxWidth: 320 }}>
      <Typography sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}>
        {link.title}
      </Typography>
      {link.detail && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {link.detail}
        </Typography>
      )}
      {keywords.length > 0 && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {`Keywords: ${keywords.join(", ")}`}
        </Typography>
      )}
      {/* Generated or inferred facts are an assumption, not a cited source. */}
      {link.assumption && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {`Assumption — ${link.assumption.charAt(0).toLowerCase()}${link.assumption.slice(1)} Cite a source to ground it.`}
        </Typography>
      )}
      {/* Built from production: the problem, not the person. */}
      {personalDataLine(scenario) && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {`${personalDataLine(scenario)}. ${personalDataDetail(scenario) || ""}`}
        </Typography>
      )}
      {scenario.reproduce?.state && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {`${REPRO_LABEL[scenario.reproduce.state]}${scenario.reproduce.of ? ` · failed ${scenario.reproduce.fails} of ${scenario.reproduce.of}` : ""}${scenario.target ? " · a target" : ""}`}
        </Typography>
      )}
      {/* Built from production: the conversation itself isn't copied here. */}
      {link.observe && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {`${link.observe.line} `}
          <Box
            component={RouterLink}
            to={link.observe.to}
            onClick={(e) => e.stopPropagation()}
            sx={{
              color: "inherit",
              fontWeight: 600,
              textDecoration: "underline",
            }}
          >
            Open the trace
          </Box>
        </Typography>
      )}
      {linked && (
        <Typography sx={{ typography: "s3", color: "inherit", opacity: 0.7 }}>
          {link.chat
            ? "Click to open the chat."
            : !link.to && link.observe
              ? "Click to open the trace."
              : link.source.id === "error-feed"
                ? "Click to open the issue."
                : link.source.id === "improvement"
                  ? "Click to open the epoch."
                  : "Click to open the run."}
        </Typography>
      )}
    </Stack>
  );

  /* In the drawer: the source named in words, with its id when it has one. */
  const refShown =
    link.ref && !link.label.toLowerCase().includes(link.ref.toLowerCase())
      ? link.ref
      : null;
  /* Added from outside (production, an Error Feed issue, a dataset, a
     transcript): its keywords are what the row shows, so those scenarios
     stay findable and the Keywords filter matches what you read. One on the
     row, "+N" for the rest; the tooltip names them all and keeps the id. */
  const keyword = keywords.length
    ? `${keywords[0]}${keywords.length > 1 ? ` +${keywords.length - 1}` : ""}`
    : null;
  const chipText = labelled
    ? `${link.label}${refShown ? ` · ${refShown}` : ""}${keywords.length ? ` · ${keywords.join(", ")}` : ""}${link.assumption ? " · Assumption" : ""}`
    : keyword ||
      (link.assumption && !linked
        ? "Assumption"
        : link.ref || SHORT[link.label] || link.label);

  if (!linked) {
    /* Nowhere to go back to (a replayed production conversation, a
       generated scenario): the chip unlinked — its id when it has one, else
       the icon alone. */
    return (
      <Tooltip arrow title={title}>
        <Box
          component="span"
          aria-label={link.title}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.375,
            flexShrink: 0,
            height: labelled ? 20 : 16,
            /* Plain text in a row: a table cell carries no boxes. */
            color: "text.tertiary",
          }}
        >
          {labelled && <Iconify icon={link.source.icon} width={12} />}
          {chipText && (
            <Typography
              component="span"
              noWrap
              sx={{
                typography: "s3",
                fontWeight: 400,
                color: "inherit",
                lineHeight: 1,
              }}
            >
              {chipText}
            </Typography>
          )}
        </Box>
      </Tooltip>
    );
  }

  /* Rows open on click — the badge's own click must not. */
  const stop = (e) => e.stopPropagation();
  const routeProps = to
    ? { component: RouterLink, to, onClick: stop }
    : {
        component: "button",
        type: "button",
        onClick: (e) => {
          stop(e);
          openBuilderChat();
        },
      };

  return (
    <Tooltip arrow title={title}>
      <Box
        {...routeProps}
        aria-label={link.title}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.375,
          flexShrink: 0,
          height: labelled ? 20 : 16,
          p: 0,
          m: 0,
          borderRadius: "4px",
          border: 0,
          bgcolor: "transparent",
          color: "text.tertiary",
          textDecoration: "none",
          cursor: "pointer",
          font: "inherit",
          transition: "color .12s ease",
          "&:hover": { color: "text.primary", textDecoration: "underline" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 1,
          },
        }}
      >
        <Iconify icon={link.source.icon} width={labelled ? 12 : 10} />
        <Typography
          component="span"
          noWrap
          sx={{
            typography: "s3",
            fontWeight: 400,
            color: "inherit",
            lineHeight: 1,
          }}
        >
          {chipText}
        </Typography>
      </Box>
    </Tooltip>
  );
}
SourceBadge.propTypes = {
  labelled: PropTypes.bool,
  scenario: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
};
