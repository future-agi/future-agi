import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import CodeBlock from "src/components/code-block";
import SettingsSection from "../SettingsSection";
import { foundValuesOf } from "../foundFacts";
import { connectionSpecOf, probeOf } from "../../_mock/connectionCheck";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * The last connection check's probe — request and reply with secrets as ••••,
 * and the tool spans seen. Behind a toggle; only once a check has run.
 */
export default function ProbeDetails({ agent }) {
  const [open, setOpen] = useState(false);
  const test = agent?.values?.connectionTest;
  if (!test?.results?.length) return null;
  const found = foundValuesOf(agent);
  const spec = connectionSpecOf(agent, found);
  if (spec.method === "async") return null;
  const probe = probeOf(agent, spec, found.tools || [], test.results);
  const json = (v) => JSON.stringify(v, null, 2);
  return (
    <SettingsSection title="Probe" sub="Secrets and personal data are masked">
      <Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setOpen(!open)}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          {open ? "Hide details" : "Show details"}
        </Button>
      </Box>
      {open && (
        <>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
              gap: 1.5,
            }}
          >
            <Box>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}
              >
                {`Request · ${probe.request.method} ${probe.request.url}`}
              </Typography>
              <CodeBlock
                code={json({
                  headers: probe.request.headers,
                  body: probe.request.body,
                })}
              />
            </Box>
            <Box>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}
              >
                {`Response · ${probe.response.status || "no reply"} · ${probe.response.ms} ms`}
              </Typography>
              <CodeBlock code={json(probe.response.body)} />
            </Box>
          </Box>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "hidden",
              "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            {probe.spans.map((s) => (
              <Stack
                key={s.name}
                direction="row"
                alignItems="center"
                spacing={2}
                sx={{ px: 2, py: 1 }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    fontFamily: MONO,
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {s.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: s.ok ? "text.secondary" : "status.danger.fg",
                    width: 56,
                  }}
                >
                  {s.ok ? "ok" : "failed"}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    color: "text.secondary",
                    width: 64,
                    textAlign: "right",
                  }}
                >
                  {`${s.ms} ms`}
                </Typography>
              </Stack>
            ))}
          </Box>
        </>
      )}
    </SettingsSection>
  );
}
ProbeDetails.propTypes = { agent: PropTypes.object };
