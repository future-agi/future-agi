import { Box, Button, Typography } from "@mui/material";
import { useState } from "react";
import SvgColor from "src/components/svg-color";
import GraphBuilderDrawer from "../GraphBuilder/GraphBuilderDrawer";

const ScenarioGraphBuilder = () => {
  const [open, setOpen] = useState(false);
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        display: "flex",
        flexDirection: "column",
        flex: 1,
        justifyContent: "flex-end",
        padding: 2,
        alignItems: "center",
        gap: 2,
      }}
    >
      <SvgColor
        src={`/assets/icons/navbar/ic_evaluate.svg`}
        sx={{ width: "36px", height: "36px", color: "text.disabled" }}
      />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 0.5,
          alignItems: "center",
        }}
      >
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          Graph Builder
        </Typography>
        <Typography typography="s2" color="text.secondary">
          Use our visual graph builder to create conversation flows
        </Typography>
      </Box>

      <Button
        color="primary"
        variant="contained"
        size="small"
        sx={{ width: "fit-content" }}
        onClick={() => setOpen(true)}
      >
        Open Graph Builder
      </Button>
      <GraphBuilderDrawer open={open} onClose={() => setOpen(false)} />
    </Box>
  );
};

export default ScenarioGraphBuilder;
