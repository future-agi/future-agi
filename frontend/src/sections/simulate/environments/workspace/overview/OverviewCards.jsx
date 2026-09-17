import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import SectionCard from "../../components/SectionCard";
import OriginChip from "../../components/OriginChip";
import { ENV_SHAPE, OVERVIEW_COPY, ruleRowsFor } from "./overview.constants";

const MONO = "ui-monospace, Menlo, monospace";

// Tools declared by the connected agent, with the arguments each really takes.
// Before an agent connects the world has no tool inventory, so a slim empty row
// stands in — its "Connect agent" affordance is deferred (there is no agent tab
// yet), so it renders disabled behind the coming-soon tooltip.
export function ToolsCard({ env, agentConnected }) {
  const tools = env.tools || [];
  return (
    <SectionCard
      title={OVERVIEW_COPY.toolsTitle}
      subtitle={agentConnected ? OVERVIEW_COPY.toolsConnected(tools.length) : OVERVIEW_COPY.toolsDisconnected}
    >
      {!agentConnected ? (
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2 }}>
          <Iconify icon="solar:settings-minimalistic-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{OVERVIEW_COPY.noToolsTitle}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{OVERVIEW_COPY.noToolsBody}</Typography>
          </Box>
          <CustomTooltip show size="small" title={OVERVIEW_COPY.agentVersionsSoon} arrow>
            <span>
              <Button
                size="small" variant="outlined" disabled aria-label={OVERVIEW_COPY.connectAgent}
                sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary", borderColor: "divider" }}
              >
                {OVERVIEW_COPY.connectAgent}
              </Button>
            </span>
          </CustomTooltip>
        </Stack>
      ) : (
        <Stack
          divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
          sx={{ maxHeight: 360, overflowY: "auto" }}
        >
          {tools.map((t) => (
            <Stack key={t.name} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.125 }}>
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", fontFamily: MONO }}>{t.name}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{t.desc}</Typography>
              </Box>
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, fontFamily: MONO }}>
                {t.args?.length ? t.args.join(", ") : OVERVIEW_COPY.noArguments}
              </Typography>
            </Stack>
          ))}
        </Stack>
      )}
    </SectionCard>
  );
}
ToolsCard.propTypes = { env: ENV_SHAPE.isRequired, agentConnected: PropTypes.bool };

// The hard rules told to the agent and graded afterwards, each carrying an
// origin chip for where it was read. The designer's "N held" review action
// routed to SourceToSandboxMap; the map lists the same held provenance below,
// and the PROSE chip already surfaces it here, so the action stays omitted.
export function HardRulesCard({ env }) {
  const rules = ruleRowsFor(env);
  return (
    <SectionCard title={OVERVIEW_COPY.hardRulesTitle} subtitle={OVERVIEW_COPY.hardRulesSubtitle}>
      <Stack sx={{ p: 2.5 }} spacing={1.25}>
        {rules.length === 0 && (
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <Iconify icon="solar:shield-check-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{OVERVIEW_COPY.noRules}</Typography>
          </Stack>
        )}
        {rules.map((r) => (
          <Stack key={r.id} direction="row" spacing={1.25} alignItems="flex-start">
            <Iconify icon="solar:shield-check-linear" width={15} sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }} />
            <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>{r.subject}</Typography>
            <OriginChip origin={r.origin} file={r.file} line={r.line} showPath={false} />
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}
HardRulesCard.propTypes = { env: ENV_SHAPE.isRequired };

// What the environment is actually for.
export function UseCasesCard({ useCases }) {
  return (
    <SectionCard title={OVERVIEW_COPY.useCasesTitle} subtitle={OVERVIEW_COPY.useCasesSubtitle}>
      <Stack sx={{ p: 2.5 }} spacing={1}>
        {(useCases || []).map((u) => (
          <Stack key={u} direction="row" spacing={1.25} alignItems="flex-start">
            <Box sx={{ width: 4, height: 4, borderRadius: "50%", bgcolor: "text.subtitle", flexShrink: 0, mt: "7px" }} />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{u}</Typography>
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}
UseCasesCard.propTypes = { useCases: PropTypes.arrayOf(PropTypes.string) };

// Facts changed after reading the source, each with its stated reason.
export function AmendmentsCard({ amendments }) {
  return (
    <SectionCard title={OVERVIEW_COPY.amendmentsTitle} subtitle={OVERVIEW_COPY.amendmentsSubtitle}>
      <Stack sx={{ p: 2.5 }} spacing={1.25}>
        {(amendments || []).map((a) => (
          <Box
            key={a.subject}
            sx={{
              p: 1.5, borderRadius: 1, border: "1px solid",
              borderColor: alpha(BUILD_TONES.amber, 0.3),
              bgcolor: (t) => alpha(BUILD_TONES.amber, t.palette.mode === "dark" ? 0.1 : 0.05),
            }}
          >
            <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", fontFamily: MONO }}>{a.subject}</Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>{a.note}</Typography>
          </Box>
        ))}
      </Stack>
    </SectionCard>
  );
}
AmendmentsCard.propTypes = {
  amendments: PropTypes.arrayOf(PropTypes.shape({ subject: PropTypes.string, note: PropTypes.string })),
};
