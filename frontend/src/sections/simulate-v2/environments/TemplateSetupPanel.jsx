import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, Divider } from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface } from "../_mock/surfaces";
import { packStats } from "../_mock/scenarios";
import { SectionCard, CopyField } from "../components/primitives";

/**
 * How to set up the selected template.
 *
 * A summary of what you picked, then the Omega CLI steps that turn it into a
 * running environment in your own repo.
 */
export default function TemplateSetupPanel({ env }) {
  if (!env) return null;

  const surface = getSurface(env.surface);
  const stats = packStats(env);
  const rows = env.seed?.tables?.reduce((a, t) => a + t.rows, 0) || 0;
  const slug = env.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");

  const steps = [
    {
      n: 1,
      title: "Initialize",
      body: "Scaffold the environment and its scenario packs into your repo.",
      cmd: `omega env init ${slug} --template ${env.id}`,
    },
    {
      n: 2,
      title: "Run a simulation",
      body: "Point it at your agent and run the core pack locally.",
      cmd: `omega sim run --env ${slug} --pack core`,
    },
    {
      n: 3,
      title: "Deploy",
      body: "Publish so runs execute on our infrastructure and traces land here.",
      cmd: `omega env deploy ${slug}`,
    },
  ];

  return (
    <Stack spacing={2}>
      {/* ── what you picked ── */}
      <SectionCard>
        <Box sx={{ p: 2.5 }}>
          <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{ mb: 1.5 }}>
            <Box
              sx={{
                width: 36, height: 36, borderRadius: 1, flexShrink: 0,
                display: "grid", placeItems: "center",
                color: "text.secondary",
                bgcolor: "background.neutral",
              }}
            >
              <Iconify icon={surface.icon} width={19} />
            </Box>
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>{env.name}</Typography>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{env.tagline}</Typography>
            </Box>
          </Stack>

          <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1.75 }}>
            {env.description}
          </Typography>

          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {surface.label} · {rows.toLocaleString()} seed rows · {stats.scenarios} scenarios ·{" "}
            {env.difficulty}
          </Typography>
        </Box>
      </SectionCard>

      {/* ── or do it from a terminal ── */}
      <SectionCard
        title="Develop locally"
        subtitle="Use the Omega CLI to scaffold, iterate and deploy from your machine"
      >
        <Stack sx={{ p: 2.5 }} spacing={0}>
          {steps.map((s, i) => (
            <Stack key={s.n} direction="row" spacing={1.75}>
              {/* step rail */}
              <Stack alignItems="center" sx={{ flexShrink: 0 }}>
                <Box
                  sx={{
                    width: 24, height: 24, borderRadius: "50%", display: "grid", placeItems: "center",
                    border: "1px solid", borderColor: "divider",
                    typography: "s3", fontWeight: 700, color: "text.secondary",
                  }}
                >
                  {s.n}
                </Box>
                {i < steps.length - 1 && (
                  <Box sx={{ flex: 1, width: "2px", bgcolor: "divider", my: 0.75, minHeight: 24 }} />
                )}
              </Stack>

              <Box sx={{ flex: 1, minWidth: 0, pb: i < steps.length - 1 ? 2.25 : 0 }}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>{s.title}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
                  {s.body}
                </Typography>
                <CopyField value={s.cmd} wrap />
              </Box>
            </Stack>
          ))}
        </Stack>

        <Divider />
        <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2.5, py: 1.75 }}>
          <Iconify icon="solar:book-linear" width={15} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
            Not installed? <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>pip install omega-cli</Box>
          </Typography>
          <Button size="small" sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}>
            Docs
          </Button>
        </Stack>
      </SectionCard>
    </Stack>
  );
}

TemplateSetupPanel.propTypes = { env: PropTypes.object };

