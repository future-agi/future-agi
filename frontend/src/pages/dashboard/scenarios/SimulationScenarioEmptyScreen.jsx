import { Box, Button, Grid, Skeleton, Typography, Link } from "@mui/material";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { LIST_ITEMS } from "./common";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { useNavigate } from "react-router-dom";

const SimulationScenarioEmptyScreen = () => {
  const [isLoading, setIsLoading] = useState(true);
  const { role } = useAuthContext();
  const navigate = useNavigate();

  const handleAddScenario = () => {
    trackEvent(Events.scenarioAddClicked, {
      [PropertyName.click]: true,
    });
    navigate("/dashboard/simulate/scenarios/create");
  };

  return (
    <Box
      sx={{
        pt: "40px",
      }}
    >
      {/* Two-section Grid layout */}
      <Grid container spacing={2}>
        {/* Left Section */}
        <Grid
          item
          xs={12}
          md={8}
          sx={{
            display: "flex",
            justifyContent: "center",
          }}
        >
          <Box
            sx={{
              width: "100%",
              height: "100%",
              minHeight: "35vh",
            }}
          >
            <div
              style={{
                position: "relative",
                paddingBottom: "calc(53.0625% + 41px)",
                height: 0,
                width: "100%",
              }}
            >
              {isLoading && (
                <Skeleton
                  variant="rectangular"
                  sx={{
                    position: "absolute",
                    top: 22,
                    left: 0,
                    width: "100%",
                    height: "93%",
                    borderRadius: "8px",
                  }}
                />
              )}
              <iframe
                src="https://www.loom.com/embed/f13e7911fb5c4ec583c72dc20acbc83a"
                title="Knowledge Base"
                frameBorder="0"
                loading="lazy"
                allow="clipboard-write"
                allowFullScreen
                onLoad={() => setIsLoading(false)}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  colorScheme: "light",
                  borderRadius: "8px",
                  opacity: isLoading ? 0 : 1,
                  transition: "opacity 0.3s ease-in-out",
                }}
              />
            </div>
          </Box>
        </Grid>

        {/* Right Section */}
        <Grid item xs={12} md={4}>
          <Box
            display="flex"
            alignItems="center"
            gap={2}
            flexDirection={"column"}
            sx={{
              mt: 2.5,
            }}
          >
            {LIST_ITEMS.map((step, index) => (
              <Box
                key={step.title}
                display="flex"
                alignItems="flex-start"
                gap={2}
                border={"1px solid"}
                borderColor="divider"
                borderRadius="4px"
                p={2}
              >
                <Typography
                  variant="m3"
                  sx={{
                    fontWeight: "fontWeightMedium",
                    backgroundColor: "background.neutral",
                    color: "text.primary",
                    padding: "7px 15px",
                    width: "40px",
                    height: "40px",
                    borderRadius: "100%",
                  }}
                >
                  {index + 1}
                </Typography>
                <Box
                  sx={{ display: "flex", flexDirection: "column", gap: "2px" }}
                >
                  <Typography
                    variant="m3"
                    sx={{
                      fontWeight: "fontWeightMedium",
                      color: "text.primary",
                    }}
                  >
                    {step.title}
                  </Typography>
                  <Typography
                    variant="s1"
                    sx={{
                      fontWeight: "fontWeightRegular",
                      color: "text.primary",
                    }}
                  >
                    {step.description}
                  </Typography>
                </Box>
              </Box>
            ))}

            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "12px",
                width: "100%",
              }}
            >
              <Button
                variant="contained"
                color="primary"
                sx={{
                  px: "24px",
                  borderRadius: "4px",
                  height: "38px",
                  maxWidth: "348px",
                  width: "100%",
                }}
                startIcon={
                  <Iconify
                    icon="octicon:plus-24"
                    color="background.paper"
                    sx={{
                      width: "20px",
                      height: "20px",
                    }}
                  />
                }
                disabled={
                  !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
                }
                onClick={handleAddScenario}
              >
                <Typography typography="s1" fontWeight={"fontWeightSemiBold"}>
                  Add Scenario
                </Typography>
              </Button>

              <Typography
                variant="s1"
                fontWeight={"fontWeightMedium"}
                color="text.disabled"
              >
                For more instructions, check out our{" "}
                <Link
                  target="_blank"
                  variant="s1"
                  fontWeight={"fontWeightSemiBold"}
                  href="https://docs.futureagi.com/docs/simulation/concepts/scenarios"
                  sx={{ textDecoration: "underline", color: "blue.500" }}
                >
                  Docs
                </Link>
              </Typography>
            </Box>
          </Box>
        </Grid>
      </Grid>
    </Box>
  );
};

export default SimulationScenarioEmptyScreen;
