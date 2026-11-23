import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { MONO, WORLD_COPY, toolNamesOf } from "./world.constants";

const C = WORLD_COPY.noWorld;

export default function ConnectOnlyWorld({ env, sx }) {
  const names = toolNamesOf(env);
  const rows = [
    {
      label: C.tools(names.length),
      value: names.length ? (
        <>
          {C.toolsLead}
          <Box
            component="span"
            sx={{ fontFamily: MONO, color: "text.primary" }}
          >
            {names.join(", ")}
          </Box>
          {C.toolsTail}
        </>
      ) : (
        C.noTools
      ),
    },
    { label: C.data, value: C.dataValue },
  ];

  return (
    <SectionCard roomy title={C.title} subtitle={C.subtitle} sx={sx}>
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {rows.map((row) => (
          <Box
            key={row.label}
            sx={{
              display: "grid",
              gridTemplateColumns: "120px minmax(0, 1fr)",
              columnGap: 2,
              px: 2.5,
              py: 1.25,
            }}
          >
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {row.label}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {row.value}
            </Typography>
          </Box>
        ))}
      </Stack>
    </SectionCard>
  );
}

ConnectOnlyWorld.propTypes = {
  env: PropTypes.object,
  sx: PropTypes.object,
};
