import { useEffect, useRef } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import TemplateHeroCard from "./cards/TemplateHeroCard";
import WebEnvironmentsHeroCard from "./cards/WebEnvironmentsHeroCard";
import OptionCard from "./cards/OptionCard";
import FlowPanel from "./panels/FlowPanel";
import { useEnvironmentsStoreShallow } from "./store/useEnvironmentsStore";
import {
  OPTIONS,
  OPTION_STATUS,
  BRING_YOUR_AGENT_ORDER,
  ENVIRONMENTS_HEADER,
} from "./environmentOptions";

const BRING_YOUR_AGENT = BRING_YOUR_AGENT_ORDER.map((id) =>
  OPTIONS.find((o) => o.id === id),
);

export default function BuildEnvironmentTab() {
  const navigate = useNavigate();
  const panelRef = useRef(null);
  const scrollTimer = useRef(null);
  const { choice, setChoice } = useEnvironmentsStoreShallow((s) => ({
    choice: s.choice,
    setChoice: s.setChoice,
  }));

  useEffect(() => () => clearTimeout(scrollTimer.current), []);

  const pick = (id) => {
    const opt = OPTIONS.find((o) => o.id === id);
    if (!opt || opt.status === OPTION_STATUS.COMING_SOON) return;
    setChoice(id);
    /* Reveal → scroll the panel into a comfortable read position. Timeout
       gives React one paint to render the panel before we scroll to it. */
    clearTimeout(scrollTimer.current);
    scrollTimer.current = setTimeout(() => {
      panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  };

  return (
    <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
      <Stack spacing={2.5}>
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          }}
        >
          <TemplateHeroCard
            selected={false}
            onClick={() => navigate(paths.dashboard.simulate.environments.templates)}
          />
          <WebEnvironmentsHeroCard />
        </Box>
        <Box>
          <Stack direction="row" alignItems="baseline" spacing={1.5} sx={{ mb: 1.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", letterSpacing: 0.4, textTransform: "uppercase" }}>
              {ENVIRONMENTS_HEADER.connectHeading}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {ENVIRONMENTS_HEADER.connectSub}
            </Typography>
          </Stack>
          <Box
            sx={{
              display: "grid",
              gap: 1.25,
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "repeat(3, 1fr)" },
            }}
          >
            {BRING_YOUR_AGENT.map((opt) => (
              <OptionCard
                key={opt.id}
                option={opt}
                selected={choice === opt.id}
                onClick={() => pick(opt.id)}
              />
            ))}
          </Box>
        </Box>
      </Stack>

      {choice && (
        <Box ref={panelRef} sx={{ mt: 2.5 }}>
          <FlowPanel choice={choice} />
        </Box>
      )}
    </Box>
  );
}
