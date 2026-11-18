import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { estimateRunPlan, rangeLabel } from "../_mock/pricing";
import { resolveEval } from "../_mock/evals";
import { worldFactsOf } from "../_mock/catalogue";
import { worldPacksOf } from "./worldSource";
import { simSitesOf } from "../_mock/simSites";
import { SHOW_RUN_COST } from "../_mock/costFlags";

/* A spec-sheet section: a title, then rows or text. */
function Section({ title, aside, children }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ mb: 0.75 }}
      >
        <Typography sx={{ typography: "m3", fontWeight: 500, flex: 1 }}>
          {title}
        </Typography>
        {aside && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {aside}
          </Typography>
        )}
      </Stack>
      {children}
    </Box>
  );
}
Section.propTypes = {
  title: PropTypes.string,
  aside: PropTypes.string,
  children: PropTypes.node,
};

/* Rows in one bordered panel: a label column and a value. */
function Rows({ rows, mono = false, labelWidth = 150 }) {
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      {rows.map((r, i) => (
        <Stack
          key={`${r.k}-${i}`}
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.75,
            ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
          }}
        >
          <Typography
            sx={{
              typography: "s1",
              width: labelWidth,
              flexShrink: 0,
              minWidth: 0,
              wordBreak: "break-word",
              ...(mono && { fontFamily: "monospace" }),
            }}
          >
            {r.k}
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {r.v}
          </Typography>
          {r.n != null && (
            <Typography
              sx={{
                typography: "s1",
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
                flexShrink: 0,
              }}
            >
              {r.n}
            </Typography>
          )}
        </Stack>
      ))}
    </Box>
  );
}
Rows.propTypes = {
  rows: PropTypes.array.isRequired,
  mono: PropTypes.bool,
  labelWidth: PropTypes.number,
};

const ATTEMPTS = 3;
const costOf = (template, count) => {
  const e = estimateRunPlan({
    env: { surface: template.surface },
    scenarios: count,
    attempts: ATTEMPTS,
    evals: (template.evalPreset || []).map(resolveEval).filter(Boolean),
  });
  return rangeLabel(e.platform.low, e.platform.high, e.platform.unknown);
};

/**
 * The preview a world opens on (catalogue.md "Discover and preview a
 * world"): what the agent will do, representative requests, seeded records,
 * tools, policies, actors, checks, and each pack's size and cost — the whole
 * task package, not a card that only says its domain.
 */
export default function WorldPreview({ world }) {
  const t = world.template;
  const entry = world.entry || null;
  const packs = worldPacksOf(t);
  const checks = (t.evalPreset || []).map(resolveEval).filter(Boolean);
  const facts = worldFactsOf(entry, t);
  const sites = simSitesOf(t.id);
  const [more, setMore] = useState(false);
  const tables = t.seed?.tables || [];
  const tools = t.tools || [];
  const rules = t.rules || [];
  const people = (entry?.actors || []).map((a) => a.split(" — ")[0]);
  const all = packs.find((p) => p.id === "full") || packs[packs.length - 1];
  /* What's in it, one line each — the detail stays a click away. */
  const summary = [
    tables.length && {
      k: "Records",
      v: tables
        .map((x) => `${x.name} ${(x.rows || 0).toLocaleString()}`)
        .join(" · "),
    },
    sites.length && {
      k: "Websites",
      v: `${sites.map((x) => x.name).join(", ")} — reset before every trial`,
    },
    tools.length && { k: "Tools", v: tools.map((x) => x.name).join(", ") },
    rules.length && {
      k: "Rules",
      v: `${rules.length} — each a critical check`,
    },
    people.length && { k: "Other parties", v: people.join(", ") },
    checks.length && { k: "Checks", v: checks.map((c) => c.name).join(", ") },
    all && {
      k: "Scenarios",
      v: `${all.count} in ${Math.max(packs.length - 2, 1)} packs · starter suite ${packs[0]?.count || 0}`,
    },
  ].filter(Boolean);
  return (
    <Stack spacing={2.5}>
      {/* The world in a few lines: what the agent does, where it's from, a request. */}
      <Box>
        <Typography sx={{ typography: "s1", color: "text.primary" }}>
          {t.description}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
          {[facts.source, facts.tested].filter(Boolean).join(" · ")}
        </Typography>
        {entry?.requests?.[0] && (
          <Typography
            sx={{
              typography: "s1",
              color: "text.secondary",
              mt: 1.25,
              pl: 1.25,
              borderLeft: "2px solid",
              borderColor: "divider",
            }}
          >
            {`“${entry.requests[0]}”`}
          </Typography>
        )}
      </Box>
      <Section title="In this world">
        <Rows rows={summary} labelWidth={110} />
      </Section>
      <Box>
        <Button
          size="small"
          color="inherit"
          onClick={() => setMore((v) => !v)}
          endIcon={
            <Iconify
              icon={
                more
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
            />
          }
          sx={{ px: 0, minWidth: 0, color: "text.secondary" }}
        >
          {more ? "Hide details" : "Show details"}
        </Button>
      </Box>
      <Collapse in={more} unmountOnExit>
        <Stack spacing={2.5}>
          <Section title="Source and support">
            <Rows
              rows={[
                { k: "Source", v: facts.source },
                { k: "Native scoring", v: facts.scoring },
                { k: "Tested support", v: facts.tested },
              ]}
            />
          </Section>
          <Section title="What the agent does">
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              {t.description}
            </Typography>
          </Section>
          {entry?.requests?.length > 0 && (
            <Section title="Example requests">
              <Stack spacing={0.75}>
                {entry.requests.map((q) => (
                  <Typography
                    key={q}
                    sx={{
                      typography: "s1",
                      color: "text.primary",
                      pl: 1.25,
                      borderLeft: "2px solid",
                      borderColor: "divider",
                    }}
                  >
                    {`“${q}”`}
                  </Typography>
                ))}
              </Stack>
            </Section>
          )}
          {/* T47: the sites this world serves the agent's browser. */}
          {sites.length > 0 && (
            <Section
              title="Simulated websites"
              aside="Reset before every trial; changes are recorded"
            >
              <Rows
                rows={sites.map((x) => ({
                  k: x.host,
                  v: `${x.name} · ${x.kind} · records ${x.records.join(", ")}`,
                  n: `${x.pages} ${x.pages === 1 ? "page" : "pages"}`,
                }))}
                mono
                labelWidth={190}
              />
            </Section>
          )}
          <Section
            title="Seeded records"
            aside="Synthetic — the world never touches production"
          >
            <Rows
              rows={(t.seed?.tables || []).map((x) => ({
                k: x.name,
                v: x.note || "",
                n: x.rows.toLocaleString(),
              }))}
              mono
            />
          </Section>
          <Section
            title="Tools"
            aside={`${(t.tools || []).length} the world answers`}
          >
            <Rows
              rows={(t.tools || []).map((x) => ({ k: x.name, v: x.desc }))}
              mono
              labelWidth={190}
            />
          </Section>
          <Section title="Rules" aside="Each is a critical check">
            <Rows
              rows={(t.rules || []).map((r, i) => ({ k: `${i + 1}`, v: r }))}
              labelWidth={16}
            />
          </Section>
          {entry?.actors?.length > 0 && (
            <Section title="Other parties">
              {/* Name — role when the catalogue gives one; otherwise the whole line. */}
              <Rows
                rows={entry.actors.map((a) => {
                  const [k, ...v] = a.split(" — ");
                  return v.length ? { k, v: v.join(" — ") } : { k: a, v: "" };
                })}
                labelWidth={
                  entry.actors.every((a) => a.includes(" — ")) ? 150 : 520
                }
              />
            </Section>
          )}
          {checks.length > 0 && (
            <Section title="Checks" aside="Run on every scenario">
              <Rows
                rows={checks.map((c) => ({
                  k: c.name,
                  v: c.description || c.type || "",
                }))}
              />
            </Section>
          )}
          <Section
            title="Scenario packs"
            aside={
              SHOW_RUN_COST
                ? `× ${ATTEMPTS} attempts · cost per run`
                : undefined
            }
          >
            <Rows
              rows={packs.map((p) => ({
                k: p.name,
                v: `${p.count} scenarios · ${p.blurb}`,
                n: SHOW_RUN_COST ? costOf(t, p.count) : null,
              }))}
            />
          </Section>
        </Stack>
      </Collapse>
    </Stack>
  );
}
WorldPreview.propTypes = { world: PropTypes.object.isRequired };
