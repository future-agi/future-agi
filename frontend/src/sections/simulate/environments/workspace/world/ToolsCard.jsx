import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import SectionCard from "../../components/SectionCard";
import {
  MONO,
  TOOLS_CARD_COPY as T,
  WORLD_CARD_COPY as C,
  groupToolsByStatus,
} from "./world.constants";

export default function ToolsCard({ tools, openTool, onOpenTool, sx }) {
  const groups = groupToolsByStatus(tools);
  return (
    <SectionCard
      roomy
      title={T.title(tools.length)}
      subtitle={T.subtitle}
      sx={sx}
    >
      {groups.map((group, index) => {
        const tier = C.tier[group.status] || C.tier.unknown;
        return (
          <Box key={group.status} role="group" aria-label={tier.word}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2,
                py: 0.75,
                bgcolor: "background.neutral",
                borderTop: index ? "1px solid" : "none",
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{ typography: "s3", fontWeight: 600, flexShrink: 0 }}
              >
                {tier.word}
              </Typography>
              <OverflowTooltip
                title={`${tier.note} · ${T.groupCount(group.tools.length)}`}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {`${tier.note} · ${T.groupCount(group.tools.length)}`}
                </Typography>
              </OverflowTooltip>
            </Stack>
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
            >
              {group.tools.map((tool) => {
                const open = openTool === tool.tool;
                return (
                  <ButtonBase
                    key={tool.tool}
                    onClick={() => onOpenTool(tool)}
                    aria-label={T.expand(tool.tool)}
                    sx={{
                      px: 2,
                      minHeight: 36,
                      gap: 1.5,
                      justifyContent: "flex-start",
                      bgcolor: open ? "action.selected" : "transparent",
                      "&:hover": {
                        bgcolor: open ? "action.selected" : "action.hover",
                      },
                    }}
                  >
                    <Iconify
                      icon="solar:alt-arrow-right-linear"
                      width={12}
                      sx={{ color: "text.tertiary", flexShrink: 0 }}
                    />
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        fontWeight: 500,
                        fontFamily: MONO,
                        width: 220,
                        flexShrink: 0,
                        textAlign: "left",
                      }}
                    >
                      {tool.tool}
                    </Typography>
                    <OverflowTooltip title={tool.description || ""}>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color: "text.secondary",
                          flex: 1,
                          minWidth: 0,
                          textAlign: "left",
                        }}
                      >
                        {tool.description}
                      </Typography>
                    </OverflowTooltip>
                  </ButtonBase>
                );
              })}
            </Stack>
          </Box>
        );
      })}
    </SectionCard>
  );
}

ToolsCard.propTypes = {
  tools: PropTypes.arrayOf(
    PropTypes.shape({
      tool: PropTypes.string.isRequired,
      status: PropTypes.string,
      description: PropTypes.string,
    }),
  ).isRequired,
  openTool: PropTypes.string,
  onOpenTool: PropTypes.func.isRequired,
  sx: PropTypes.object,
};
