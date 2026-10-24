import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import MetricStrip from "src/components/metric-strip";
import { SectionCard, OriginChip } from "../components/primitives";
import { textTone } from "../components/textTone";
import { buildRecord, provenanceFor, BUILD_ACCESS } from "../_mock/provenance";

/**
 * How this environment was built.
 *
 * The derivation streams past once in the console and then it is gone, so an
 * environment somebody set up three weeks ago arrives as a set of assertions —
 * these are your tools, these are your rules — with nothing behind them. That
 * is fine while you are watching it happen and useless afterwards, which is
 * exactly backwards: the person who most needs to check the derivation is the
 * one who did not run it.
 *
 * So this is the record. What was read, what was written, where every rule came
 * from, and what the builder itself was able to reach while it held the source.
 * The last one matters more than it looks: a build that reads a repo and writes
 * the graders is the one place where a sentence in a file could quietly decide
 * what "passing" means, and the answer to that is provenance you can see rather
 * than a promise that it did not happen.
 */
export default function BuildRecordPanel({ env, envState, patch }) {
  const record = buildRecord(env);
  const prov = provenanceFor(env);
  const confirmed = envState?.confirmedRules || [];
  const dropped = envState?.droppedRules || [];

  const pending = prov.held.filter(
    (r) => !confirmed.includes(r.id) && !dropped.includes(r.id),
  );
  const decide = (id, keep) =>
    patch?.(
      keep
        ? { confirmedRules: [...confirmed, id] }
        : { droppedRules: [...dropped, id] },
    );

  /* Every decision here is reversible. Accepting a rule read out of prose
     promotes it to a grading authority, and a promotion nobody can walk back
     is a worse control than no control — a mis-click would silently decide
     what "passing" means from then on. */
  const undo = (id) =>
    patch?.({
      confirmedRules: confirmed.filter((x) => x !== id),
      droppedRules: dropped.filter((x) => x !== id),
    });

  const decided = (id) => confirmed.includes(id) || dropped.includes(id);

  const graded = prov.rules.filter(
    (r) => !r.held || confirmed.includes(r.id),
  ).length;

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>
          How this was built
        </Typography>
        <Typography
          sx={{ typography: "s1", color: "text.secondary", maxWidth: 760 }}
        >
          The derivation that produced {env.name}, kept so it can be checked by
          someone who did not watch it run.
        </Typography>
      </Box>

      <MetricStrip
        sx={{ mb: 2 }}
        items={[
          {
            label: "Read from",
            value: record.source.label,
            hint: record.source.ref,
          },
          {
            label: "Tools",
            value: prov.tools.length,
            hint: "signatures read from source",
          },
          {
            label: "Rules graded",
            value: `${graded}/${prov.rules.length}`,
            hint: "the rest are held",
          },
          {
            label: "Held for review",
            value: pending.length,
            hint: pending.length ? "found in prose" : "nothing outstanding",
          },
        ]}
      />

      {/*
        The review queue leads when it is not empty. A rule the builder declined
        to grade against is the one thing on this screen that is waiting on a
        person, and burying it under a stage list would make it decoration.
      */}
      {pending.length > 0 && (
        <SectionCard
          title="Held back — a rule found in prose"
          subtitle="Nothing grades against these until you say so"
          sx={{ mb: 2 }}
        >
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {pending.map((r) => (
              <Stack key={r.id} spacing={1.25} sx={{ px: 2.5, py: 2 }}>
                <Stack direction="row" alignItems="flex-start" spacing={1.5}>
                  <Iconify
                    icon="solar:danger-triangle-bold"
                    width={16}
                    sx={{ color: "status.danger.fg", flexShrink: 0, mt: "2px" }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {r.subject}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s2",
                        color: "text.secondary",
                        mt: 0.25,
                      }}
                    >
                      {r.detail}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        fontFamily: "ui-monospace, Menlo, monospace",
                        mt: 0.75,
                      }}
                    >
                      {r.file}:{r.line}
                    </Typography>
                  </Box>
                </Stack>
                <Stack direction="row" spacing={1} sx={{ pl: 3.75 }}>
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    onClick={() => decide(r.id, true)}
                  >
                    This is a real rule — grade it
                  </Button>
                  <Button
                    variant="outlined"
                    color="inherit"
                    size="small"
                    onClick={() => decide(r.id, false)}
                    sx={{ borderColor: "divider" }}
                  >
                    Drop it
                  </Button>
                </Stack>
              </Stack>
            ))}
          </Stack>
        </SectionCard>
      )}

      <SectionCard
        title="What ran"
        subtitle="Three stages, each one the input to the next"
        sx={{ mb: 2 }}
      >
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {record.stages.map((s, i) => (
            <Stack
              key={s.id}
              direction="row"
              spacing={2}
              sx={{ px: 2.5, py: 1.75 }}
              alignItems="flex-start"
            >
              <Typography
                sx={{
                  width: 20,
                  flexShrink: 0,
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                  mt: "2px",
                }}
              >
                {i + 1}
              </Typography>
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {s.label}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {s.detail}
                </Typography>
                <Stack
                  direction="row"
                  spacing={2}
                  sx={{ mt: 0.75 }}
                  flexWrap="wrap"
                  rowGap={0.5}
                >
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    read{" "}
                    <Box component="span" sx={{ color: "text.secondary" }}>
                      {s.read}
                    </Box>
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    wrote{" "}
                    <Box
                      component="span"
                      sx={{
                        color: "text.secondary",
                        fontFamily: "ui-monospace, Menlo, monospace",
                      }}
                    >
                      {s.wrote}
                    </Box>
                  </Typography>
                </Stack>
              </Box>
            </Stack>
          ))}
        </Stack>
      </SectionCard>

      {/*
        Provenance in full. The summary counts first, because "three of these
        came from a code path and one came from a README" is the sentence a
        reviewer actually wants, and the rows are there for whoever doubts it.
      */}
      <SectionCard
        title="Where every fact came from"
        subtitle={
          record.derived
            ? "Each tool and rule, with the file in your source it was read out of"
            : "Each tool and rule, with the file in the reference agent this template was built from"
        }
        sx={{ mb: 2 }}
        action={
          <Stack
            direction="row"
            spacing={0.75}
            sx={{ display: { xs: "none", md: "flex" } }}
          >
            {prov.summary.map((k) => (
              <Typography
                key={k.id}
                sx={{
                  px: 0.875,
                  py: 0.375,
                  borderRadius: "4px",
                  typography: "s3",
                  fontWeight: 600,
                  color: textTone(k.color, "text.secondary"),
                  bgcolor: (t) =>
                    alpha(k.color, t.palette.mode === "dark" ? 0.16 : 0.1),
                }}
              >
                {k.count} {k.label}
              </Typography>
            ))}
          </Stack>
        }
      >
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {prov.rules.map((r) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 2.5, py: 1 }}
            >
              <Iconify
                icon="solar:shield-check-linear"
                width={14}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography
                sx={{
                  typography: "s2",
                  flex: 1,
                  minWidth: 0,
                  color: dropped.includes(r.id)
                    ? "text.disabled"
                    : "text.primary",
                  textDecoration: dropped.includes(r.id)
                    ? "line-through"
                    : "none",
                }}
              >
                {r.subject}
              </Typography>
              {r.held && (
                <Button
                  size="small"
                  onClick={() => (decided(r.id) ? undo(r.id) : undefined)}
                  disabled={!decided(r.id)}
                  sx={{
                    flexShrink: 0,
                    minWidth: 0,
                    color: confirmed.includes(r.id)
                      ? "status.success.fg"
                      : "status.danger.fg",
                    "&.Mui-disabled": { color: "status.danger.fg" },
                  }}
                >
                  {confirmed.includes(r.id)
                    ? "graded · undo"
                    : dropped.includes(r.id)
                      ? "dropped · undo"
                      : "held"}
                </Button>
              )}
              <OriginChip origin={r.origin} file={r.file} line={r.line} />
            </Stack>
          ))}
          {prov.tools.map((t) => (
            <Stack
              key={t.id}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 2.5, py: 1 }}
            >
              <Iconify
                icon="solar:widget-5-linear"
                width={14}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography
                sx={{
                  typography: "s2",
                  flex: 1,
                  minWidth: 0,
                  fontFamily: "ui-monospace, Menlo, monospace",
                }}
              >
                {t.subject}
              </Typography>
              <OriginChip origin={t.origin} file={t.file} line={t.line} />
            </Stack>
          ))}
        </Stack>
      </SectionCard>

      {/*
        The build's own sandbox. Instances answers this for the run; until now
        nothing answered it for the thing that read the source.
      */}
      <SectionCard
        title="What the builder could reach"
        subtitle={
          record.derived
            ? "While it had your source open"
            : "While it had the source open"
        }
      >
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {BUILD_ACCESS.map((g) => (
            <Stack
              key={g.id}
              direction="row"
              spacing={1.75}
              sx={{ px: 2.5, py: 1 }}
              alignItems="flex-start"
            >
              <Box
                sx={{
                  width: 28,
                  height: 28,
                  borderRadius: "6px",
                  display: "grid",
                  placeItems: "center",
                  flexShrink: 0,
                  color: "status.success.fg",
                  bgcolor: "status.success.bg",
                }}
              >
                <Iconify icon={g.icon} width={14} />
              </Box>
              <Box minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {g.label}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {g.note}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </SectionCard>
    </Box>
  );
}

BuildRecordPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
};
