import { Helmet } from "react-helmet-async";
import { Box } from "@mui/material";
import AlEnvironmentView from "src/sections/al-environment/AlEnvironmentView";

const AlEnvironment = () => (
  <>
    <Helmet>
      <title>RL Environment</title>
    </Helmet>
    <Box
      sx={{
        backgroundColor: "background.default",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <AlEnvironmentView />
    </Box>
  </>
);

export default AlEnvironment;
