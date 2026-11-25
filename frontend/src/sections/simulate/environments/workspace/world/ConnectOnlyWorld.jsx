import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { MONO, WORLD_COPY, toolNamesOf } from "./world.constants";

const C = WORLD_COPY.noWorld;

export default function ConnectOnlyWorld({ env, sx }) {
  const names = toolNamesOf(env);
  return (
    <SectionCard roomy title={C.title} subtitle={C.subtitle} sx={sx}>
      {names.length > 0 && (
        <Box
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
            {C.tools(names.length)}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {C.toolsLead}
            <Box
              component="span"
              sx={{ fontFamily: MONO, color: "text.primary" }}
            >
              {names.join(", ")}
            </Box>
            {C.toolsTail}
          </Typography>
        </Box>
      )}
    </SectionCard>
  );
}

ConnectOnlyWorld.propTypes = {
  env: PropTypes.object,
  sx: PropTypes.object,
};
