import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, ButtonBase, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";

/*
  The personas this environment's customer plays (glossary: a persona is how
  an actor behaves, not a separate thing). Each scenario sets which one, so
  this lists them from the scenarios: who, how they behave, and which
  scenarios they're in — without leaving the environment. A persona is
  changed on its scenario.
*/
export default function PersonasInUseDrawer({
  open,
  onClose,
  scenarios,
  onOpenScenarios,
}) {
  const personas = useMemo(() => {
    const by = new Map();
    scenarios.forEach((s) => {
      const p = s.persona;
      const name = p?.name || "Unnamed caller";
      const cur = by.get(name) || { name, persona: p, scenarios: [] };
      cur.scenarios.push(s.title || s.summary || s.name || s.id);
      by.set(name, cur);
    });
    return [...by.values()].sort(
      (a, b) => b.scenarios.length - a.scenarios.length,
    );
  }, [scenarios]);

  const traitsOf = (p) =>
    [
      ...(p?.traits || []).map((t) => (typeof t === "string" ? t : t?.label)),
      p?.ageGroup,
      p?.gender,
    ]
      .filter(Boolean)
      .join(" · ");

  return (
    <SideDrawer open={open} onClose={onClose} width={520}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.5}
          sx={{
            px: 2.5,
            pt: 2,
            pb: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Personas in this environment
            </Typography>
            <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
              {`How the customer behaves — ${personas.length} persona${personas.length === 1 ? "" : "s"} across ${scenarios.length} scenario${scenarios.length === 1 ? "" : "s"}. Each scenario sets which one it plays.`}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>

        <Box
          sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 2.5, py: 1.5 }}
        >
          {personas.length === 0 ? (
            <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
              No scenarios yet, so no personas. Each scenario you add sets the
              persona the customer plays in it.
            </Typography>
          ) : (
            <Box
              sx={{
                borderTop: "1px solid",
                borderColor: "divider",
                "& > *": { borderBottom: "1px solid", borderColor: "divider" },
              }}
            >
              {personas.map((p) => {
                const traits = traitsOf(p.persona);
                const shown = p.scenarios.slice(0, 3);
                return (
                  <Box key={p.name} sx={{ py: 1.25 }}>
                    <Stack direction="row" alignItems="baseline" spacing={1}>
                      <Typography
                        sx={{ typography: "s1", fontWeight: 500, flex: 1 }}
                      >
                        {p.name}
                      </Typography>
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          fontVariantNumeric: "tabular-nums",
                          flexShrink: 0,
                        }}
                      >
                        {`${p.scenarios.length} scenario${p.scenarios.length === 1 ? "" : "s"}`}
                      </Typography>
                    </Stack>
                    {traits && (
                      <Typography
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {traits}
                      </Typography>
                    )}
                    <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2 }}>
                      {shown.map((t, i) => (
                        <Typography
                          component="li"
                          key={`${p.name}-${i}`}
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {t}
                        </Typography>
                      ))}
                      {p.scenarios.length > shown.length && (
                        <Typography
                          component="li"
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            listStyle: "none",
                          }}
                        >
                          {`and ${p.scenarios.length - shown.length} more`}
                        </Typography>
                      )}
                    </Box>
                  </Box>
                );
              })}
            </Box>
          )}
        </Box>

        {onOpenScenarios && (
          <Stack
            direction="row"
            alignItems="center"
            sx={{
              px: 2.5,
              pr: 9,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{ typography: "s1", color: "text.tertiary", flex: 1 }}
            >
              A persona is changed on its scenario.
            </Typography>
            <ButtonBase
              onClick={onOpenScenarios}
              sx={{
                typography: "s1",
                fontWeight: 500,
                color: "text.primary",
                textDecoration: "underline",
                textUnderlineOffset: 3,
              }}
            >
              Open Scenarios
            </ButtonBase>
          </Stack>
        )}
      </Stack>
    </SideDrawer>
  );
}

PersonasInUseDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  scenarios: PropTypes.arrayOf(PropTypes.object).isRequired,
  onOpenScenarios: PropTypes.func,
};
