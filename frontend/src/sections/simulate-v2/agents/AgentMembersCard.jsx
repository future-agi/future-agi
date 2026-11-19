import PropTypes from "prop-types";
import { useLayoutEffect, useRef, useState } from "react";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { PanelCard, QuietTag } from "./AgentBits";
import { MONO } from "./agentViewUtils";
import { workerToolsOf } from "../_mock/handoffs";
import { NOT_CONTROLLED } from "../_mock/participants";
import { clickable, focusRing } from "../components/clickable";

const LABEL = {
  typography: "s3",
  fontWeight: 600,
  color: "text.subtitle",
  textTransform: "none",
  letterSpacing: 0.4,
};

/* Two lines, with Show all only when the text is really cut off. */
function ClampText({ text }) {
  const ref = useRef(null);
  const [full, setFull] = useState(false);
  const [cut, setCut] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !full) setCut(el.scrollHeight > el.clientHeight + 1);
  }, [text, full]);
  return (
    <>
      <Typography
        ref={ref}
        sx={{
          typography: "s2",
          mt: 0.25,
          whiteSpace: "pre-line",
          ...(!full && {
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }),
        }}
      >
        {text}
      </Typography>
      {(cut || full) && (
        <Typography
          component="span"
          {...clickable(() => setFull((v) => !v))}
          sx={{ typography: "s3", color: "primary.main", fontWeight: 600, cursor: "pointer", ...focusRing }}
        >
          {full ? "Show less" : "Show all"}
        </Typography>
      )}
    </>
  );
}
ClampText.propTypes = { text: PropTypes.string };

/**
 * Several agents working together — a Vapi squad, a Retell or ElevenLabs
 * flow, a code app with handoffs — are one agent. Each member keeps its own
 * instructions, model and tools; protected members are never changed by an
 * improvement.
 *   members   — [{ id, name, role, instructions, model, tools }]
 *   areas     — memberAreasOf(): each member's configuration areas
 *   protected — member ids the change policy protects
 */
export default function AgentMembersCard({
  members,
  areas = [],
  protectedIds = [],
  onConstraints,
  /* delegationEvidenceOf(agent): whether runs observe each member's part. */
  evidence = null,
}) {
  const [open, setOpen] = useState(
    () => new Set((members || []).slice(0, 1).map((m) => m.id)),
  );
  const areasOf = (id) => areas.find((x) => x.member.id === id)?.areas;
  const instrumented = evidence?.source === "instrumented";
  /* What a run can know about the members, as three facts; the why is one
     hover away (roadmap connect.md, Week 2). */
  const facts = evidence
    ? [
        ["Declared by", evidence.source === "provider" ? "Its platform" : instrumented ? "Its code" : "You"],
        ["Each member's part", evidence.observed ? "Observed in runs" : "Not observed"],
        ["Tool limits", instrumented ? "Enforced" : "Not enforced"],
      ]
    : [];
  const factsWhy = evidence
    ? [
        evidence.line,
        "These are real parts of your agent, not simulated people (those are the environment's Actors). Hidden grading material is kept from all of them.",
        !instrumented && `${NOT_CONTROLLED}.`,
      ].filter(Boolean)
    : [];
  if (!members?.length) return null;
  const toggle = (id) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  return (
    <PanelCard
      title={`Participants · ${members.length}`}
      note="One version for the whole agent"
    >
      {evidence && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={3}
          sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
        >
          {facts.map(([k, v]) => (
            <Box key={k}>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{k}</Typography>
              <Typography sx={{ typography: "s3", fontWeight: 500 }}>{v}</Typography>
            </Box>
          ))}
          <Tooltip
            arrow
            title={
              <Stack spacing={0.5} sx={{ py: 0.25, maxWidth: 320 }}>
                {factsWhy.map((t) => (
                  <Typography key={t} sx={{ typography: "s3", color: "inherit" }}>{t}</Typography>
                ))}
              </Stack>
            }
          >
            <Box
              component="span"
              aria-label="What this means"
              sx={{ display: "inline-flex", color: "text.subtitle", alignSelf: "flex-end", pb: 0.25 }}
            >
              <Iconify icon="solar:info-circle-linear" width={16} />
            </Box>
          </Tooltip>
        </Stack>
      )}
      {members.map((m, i) => {
        const on = open.has(m.id);
        const locked = protectedIds.includes(m.id);
        return (
          <Box
            key={m.id}
            sx={{ borderTop: i ? "1px solid" : "none", borderColor: "divider" }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.25}
              role="button"
              tabIndex={0}
              onClick={() => toggle(m.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle(m.id);
                }
              }}
              sx={{
                px: 2,
                py: 1,
                cursor: "pointer",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Iconify
                icon={
                  on
                    ? "solar:alt-arrow-down-linear"
                    : "solar:alt-arrow-right-linear"
                }
                width={14}
                sx={{ color: "text.subtitle", flexShrink: 0 }}
              />
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography
                    noWrap
                    sx={{ typography: "s2_1", fontWeight: 500 }}
                  >
                    {m.name}
                  </Typography>
                  <QuietTag>{i === 0 ? "Coordinator" : "Worker"}</QuietTag>
                  {locked && (
                    <QuietTag icon="solar:lock-keyhole-minimalistic-linear">
                      Protected
                    </QuietTag>
                  )}
                </Stack>
                {m.role && (
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.subtitle" }}
                  >
                    {m.role}
                  </Typography>
                )}
              </Box>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  color: "text.secondary",
                  flexShrink: 0,
                }}
              >
                {m.model}
              </Typography>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  flexShrink: 0,
                  width: 56,
                  textAlign: "right",
                }}
              >
                {`${m.tools.length} tool${m.tools.length === 1 ? "" : "s"}`}
              </Typography>
            </Stack>
            {on && (
              <Box sx={{ px: 2, pb: 1.5, pl: 5.25 }}>
                <Typography sx={LABEL}>Instructions</Typography>
                <ClampText text={m.instructions || "—"} />
                {!!areasOf(m.id)?.length && (
                  <Typography sx={{ ...LABEL, mt: 1.25 }}>
                    Configuration
                  </Typography>
                )}
                {!!areasOf(m.id)?.length && (
                  <Box
                    sx={{
                      mt: 0.5,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                      overflow: "hidden",
                    }}
                  >
                    {(areasOf(m.id) || [])
                      .filter(
                        (a) =>
                          a.id !== "instructions" &&
                          !(a.id !== "tools" && (!a.value || a.value === "—")) &&
                          !(a.value && a.value === m.role),
                      )
                      .map((a, j) => (
                        <Box
                          key={a.id}
                          sx={{
                            display: "grid",
                            gridTemplateColumns: "150px minmax(0, 1fr)",
                            columnGap: 1.5,
                            px: 1.25,
                            py: 0.625,
                            borderTop: j ? "1px solid" : "none",
                            borderColor: "divider",
                          }}
                        >
                          <Typography
                            sx={{ typography: "s3", color: "text.subtitle" }}
                          >
                            {a.label}
                          </Typography>
                          {a.id === "tools" && m.tools.length ? (
                            <Stack
                              direction="row"
                              spacing={0.5}
                              useFlexGap
                              flexWrap="wrap"
                            >
                              {workerToolsOf(m).map((t) => (
                                <Box
                                  key={t}
                                  sx={{
                                    px: 0.75,
                                    borderRadius: "4px",
                                    border: "1px solid",
                                    borderColor: "divider",
                                    typography: "s3",
                                    fontFamily: MONO,
                                    color: "text.secondary",
                                  }}
                                >
                                  {t}
                                </Box>
                              ))}
                              {evidence && (
                                <Typography
                                  sx={{ typography: "s3", color: "text.subtitle", alignSelf: "center" }}
                                >
                                  {instrumented
                                    ? `· others blocked${(() => {
                                        const blocked = members
                                          .filter((x) => x.id !== m.id)
                                          .flatMap((x) => workerToolsOf(x))
                                          .filter((n, k, all) => !workerToolsOf(m).includes(n) && all.indexOf(n) === k);
                                        return blocked.length ? `: ${blocked.join(", ")}` : "";
                                      })()}`
                                    : "· not enforced"}
                                </Typography>
                              )}
                            </Stack>
                          ) : (
                            <Typography
                              sx={{
                                typography: "s3",
                                color: a.hidden
                                  ? "text.subtitle"
                                  : "text.primary",
                                fontFamily: a.id === "model" ? MONO : undefined,
                              }}
                            >
                              {a.value}
                            </Typography>
                          )}
                        </Box>
                      ))}
                  </Box>
                )}
              </Box>
            )}
          </Box>
        );
      })}
      {onConstraints && (
        <Typography
          sx={{
            px: 2,
            py: 1,
            typography: "s3",
            color: "text.subtitle",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {"Protect a member in "}
          <Box
            component="span"
            {...clickable(onConstraints)}
            sx={{
              color: "primary.main",
              fontWeight: 600,
              cursor: "pointer",
              "&:hover": { textDecoration: "underline" },
              ...focusRing,
            }}
          >
            Constraints
          </Box>
          {" to keep improvements off it."}
        </Typography>
      )}
    </PanelCard>
  );
}
AgentMembersCard.propTypes = {
  evidence: PropTypes.object,
  members: PropTypes.array,
  areas: PropTypes.array,
  protectedIds: PropTypes.array,
  onConstraints: PropTypes.func,
};
