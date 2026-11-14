import React, { useState } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";

export default function VariableAccessInfo() {
  const [expanded, setExpanded] = useState(false);

  return (
    <Box
      sx={{
        borderRadius: 1,
        border: "1px solid",
        borderColor: "status.info.border",
        bgcolor: "status.info.bg",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ px: 1.25, py: 0.75, cursor: "pointer" }}
        onClick={() => setExpanded((prev) => !prev)}
      >
        <SvgColor
          src="/assets/icons/ic_info.svg"
          sx={{ width: 14, height: 14, color: "info.main", flexShrink: 0 }}
        />
        <Typography
          variant="caption"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ flex: 1 }}
        >
          How to use variables
        </Typography>
        <SvgColor
          src="/assets/icons/custom/down-chevron.svg"
          sx={{
            width: 12,
            height: 12,
            color: "info.main",
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 0.2s",
          }}
        />
      </Stack>

      <Collapse in={expanded}>
        <Box sx={{ px: 1.25, pb: 1, pt: 0.25 }}>
          <Typography
            variant="caption"
            color="text.primary"
            component="div"
            sx={{ lineHeight: 1.8, "& code": { fontSize: "0.75rem" } }}
          >
            <Box component="ul" sx={{ m: 0, pl: 2 }}>
              <li>
                Type <code>{"{{"}</code> to see available variables
              </li>
              <li>
                <code>{"{{node_name.output_label}}"}</code> — connected node
                output
              </li>
              <li>
                <code>{"{{node_name.output.some_key}}"}</code> — nested JSON key
              </li>
              <li>
                <code>{"{{custom_var}}"}</code> — dataset/global variable
              </li>
            </Box>
          </Typography>
        </Box>
      </Collapse>
    </Box>
  );
}
