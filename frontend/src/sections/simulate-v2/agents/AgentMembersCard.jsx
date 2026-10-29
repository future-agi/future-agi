import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { PanelCard, QuietTag } from "./AgentBits";
import { MONO } from "./agentViewUtils";
import { clickable, focusRing } from "../components/clickable";

const LABEL = {
  typography: "s3",
  fontWeight: 600,
  color: "text.subtitle",
  textTransform: "none",
  letterSpacing: 0.4,
};

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
      title={`Members · ${members.length}`}
      note="One agent · one version history"
    >
      {/* What a run can know about them, and what each may touch in the
          test world (roadmap connect.md, Week 2). */}
      {evidence && (
        <Stack spacing={0.5} sx={{ px: 2, py: 1.25 }}>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`Declared by ${evidence.source === "provider" ? "its platform" : evidence.source === "instrumented" ? "its code" : "you"}. ${evidence.line}`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Each member may use only its own tools, answered by the test world.
            Hidden grading material — reference answers, the end state a check
            expects — is kept from every member and the simulated callers.
          </Typography>
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
                <Typography
                  sx={{ typography: "s2", mt: 0.25, whiteSpace: "pre-line" }}
                >
                  {m.instructions || "—"}
                </Typography>
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
                      .filter((a) => a.id !== "instructions")
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
                              {m.tools.map((t) => (
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
          {"Protect a member on "}
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
          {
            " so an improvement never changes it. Analysis and Account belong to the whole agent."
          }
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
