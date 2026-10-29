import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { MONO } from "../agentViewUtils";

const rowSx = {
  px: 1.5,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};

export function FactRow({ k, children }) {
  return (
    <Stack direction="row" spacing={1.5} sx={rowSx}>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 132,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Box sx={{ typography: "s2", flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
FactRow.propTypes = { k: PropTypes.string, children: PropTypes.node };

const factBoxSx = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  bgcolor: "background.paper",
};

export function FactBox({ children }) {
  return <Box sx={factBoxSx}>{children}</Box>;
}
FactBox.propTypes = { children: PropTypes.node };

const Mono = ({ children }) => (
  <Box component="span" sx={{ fontFamily: MONO, fontSize: 12 }}>
    {children}
  </Box>
);
Mono.propTypes = { children: PropTypes.node };

/**
 * What a skill asks for, read from its files (skills.md "Review access"):
 * tools — each against the agent's own, scripts that may run, files it
 * reads, credentials it names (references only, never values).
 */
export default function SkillFacts({ meta, agentTools, runtime }) {
  const have = new Set(agentTools || []);
  const missing = meta.tools.filter((t) => !have.has(t));
  return (
    <Box sx={factBoxSx}>
      <FactRow k="When it's used">{meta.description}</FactRow>
      <FactRow k="Tools it expects">
        {meta.tools.length ? (
          <Stack spacing={0.25}>
            {meta.tools.map((t) => (
              <span key={t}>
                <Mono>{t}</Mono>
                <Box
                  component="span"
                  sx={{ color: have.has(t) ? "text.tertiary" : "warning.dark" }}
                >
                  {have.has(t)
                    ? " · the agent has it"
                    : " · the agent doesn't have it"}
                </Box>
              </span>
            ))}
          </Stack>
        ) : (
          "None declared"
        )}
      </FactRow>
      <FactRow k="Scripts that may run">
        {meta.scripts.length ? (
          <Stack spacing={0.25}>
            {meta.scripts.map((s) => (
              <span key={s.path}>
                <Mono>{s.path}</Mono>
                <Box component="span" sx={{ color: "text.tertiary" }}>
                  {` · ${s.lang} · ${s.network ? "makes network calls" : "no network calls found"}`}
                </Box>
              </span>
            ))}
          </Stack>
        ) : (
          "None — instructions only"
        )}
      </FactRow>
      <FactRow k="Files it reads">
        {meta.reads.length
          ? meta.reads.map((p, i) => (
              <span key={p}>
                {i > 0 && ", "}
                <Mono>{p}</Mono>
              </span>
            ))
          : "None"}
      </FactRow>
      <FactRow k="Credentials">
        {meta.credentials.length ? (
          <>
            {meta.credentials.map((c, i) => (
              <span key={c}>
                {i > 0 && ", "}
                <Mono>{c}</Mono>
              </span>
            ))}
            <Box component="span" sx={{ color: "text.tertiary" }}>
              {
                " · named only. The value stays in your secrets, we never store it."
              }
            </Box>
          </>
        ) : (
          "None named"
        )}
      </FactRow>
      {runtime && (
        <FactRow k="On this agent">
          {runtime.mechanism}
          <Box sx={{ color: "text.tertiary" }}>{runtime.line}</Box>
        </FactRow>
      )}
      {missing.length > 0 && (
        <FactRow k="Before it can work">
          <Box component="span" sx={{ color: "warning.dark" }}>
            {`Add ${missing.join(", ")} to the agent, or the skill's steps that call ${missing.length === 1 ? "it" : "them"} will fail.`}
          </Box>
        </FactRow>
      )}
    </Box>
  );
}

SkillFacts.propTypes = {
  meta: PropTypes.object.isRequired,
  agentTools: PropTypes.array,
  runtime: PropTypes.object,
};
