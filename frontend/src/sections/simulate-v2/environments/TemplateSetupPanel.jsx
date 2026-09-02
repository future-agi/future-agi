import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Typography, Button, Divider } from "@mui/material";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import { getSurface } from "../_mock/surfaces";
import { packStats } from "../_mock/scenarios";
import { SectionCard, CopyField } from "../components/primitives";

/**
 * How to set up the selected template.
 *
 * A summary of what you picked, then the CLI steps that turn it into a
 * running environment in your own repo.
 */
export default function TemplateSetupPanel({ env }) {
  const navigate = useNavigate();
  if (!env) return null;

  const surface = getSurface(env.surface);
  const stats = packStats(env);
  const rows = env.seed?.tables?.reduce((a, t) => a + t.rows, 0) || 0;
  const slug = env.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");

  /*
    All templates (twin or not) route through the UseTemplate wizard —
    it walks the user through connecting their agent first, then fit
    check, then commit. For twin-backed templates, `UseTemplate.finish`
    triggers the four-phase provisioning modal on commit; for non-twin
    templates the env is adopted directly. Same door, different second-
    to-last step.
  */
  const useTemplate = () => {
    navigate(paths.dashboard.simulate.environmentUseTemplate(env.id));
  };

  const steps = [
    {
      n: 1,
      title: "Initialize",
      body: "Scaffold the environment and its scenario packs into your repo.",
      cmd: `fai env init ${slug} --template ${env.id}`,
    },
    {
      n: 2,
      title: "Run a simulation",
      body: "Point it at your agent and run the core pack locally.",
      cmd: `fai sim run --env ${slug} --pack core`,
    },
    {
      n: 3,
      title: "Deploy",
      body: "Publish so runs execute on our infrastructure and traces land here.",
      cmd: `fai env deploy ${slug}`,
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
            {surface.label} ·{" "}
            {env.twinBacking
              ? `${env.twinBacking.services.length} twinned service${env.twinBacking.services.length === 1 ? "" : "s"}`
              : `${rows.toLocaleString()} seed rows`}
            {" "}· {stats.scenarios} scenarios · {env.difficulty}
          </Typography>

        </Box>

        {/*
          The primary way in. The world already exists here, so the only thing
          missing is an agent — which makes this a shorter path than building
          an environment from one.
        */}
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>Test your agent here</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Connect your agent and run this world&apos;s scenarios — nothing to scaffold
            </Typography>
          </Box>
          <Button
            variant="contained"
            color="primary"
            onClick={useTemplate}
            endIcon={<Iconify icon="solar:arrow-right-linear" width={15} />}
            sx={{ flexShrink: 0, typography: "s2", fontWeight: 700 }}
          >
            Use this template
          </Button>
        </Stack>
      </SectionCard>

      {/* ── or do it from a terminal ── */}
      <SectionCard
        title="Develop locally"
        subtitle="Or scaffold, iterate and deploy from your own machine"
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
                  <Box sx={{ flex: 1, width: "1px", bgcolor: "divider", my: 0.75, minHeight: 24 }} />
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
            Not installed? <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>pip install futureagi</Box>
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

