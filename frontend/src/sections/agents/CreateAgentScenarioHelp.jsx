import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Image from "src/components/image";
import SvgColor from "src/components/svg-color";
import { stepsInfo } from "./constants";
import { useNavigate, useParams } from "react-router";

function HelpCard({ title, description, imageSrc }) {
  const theme = useTheme();
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        sx={{
          position: "relative",
          height: "300px",
          width: "100%",
          overflow: "hidden",
          bgcolor: "background.neutral",
        }}
      >
        <Box
          sx={{
            position: "absolute",
            height: "80%",
            width: "90%",
            bottom: 0,
            right: 0,
            bgcolor: "background.paper",
            borderRadius: 1,
            padding: theme.spacing(1.5, 0, 0, 1.5),
            "& img": {
              objectFit: "contain",
            },
          }}
        >
          <Image
            src={imageSrc}
            sx={{
              width: "100%",
              height: "100%",
            }}
          />
        </Box>
      </Box>
      <Stack
        gap={0.5}
        sx={{
          padding: 2,
        }}
      >
        <Typography
          typography={"m3"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          {title}
        </Typography>
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightRegular"}
          color={"text.secondary"}
        >
          {description}
        </Typography>
      </Stack>
    </Box>
  );
}

HelpCard.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  imageSrc: PropTypes.string.isRequired,
};

export default function CreateAgentScenarioHelp({ open, onClose }) {
  const { agentDefinitionId } = useParams();
  const navigate = useNavigate();
  const handleCreateAgentScenarios = () => {
    navigate("/dashboard/simulate/scenarios/create", {
      state: {
        agentDefinitionId,
      },
    });
    onClose();
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{
        sx: {
          padding: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        },
      }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "flex-start",
          padding: 0,
        }}
      >
        <Stack direction={"column"} gap={0.25}>
          <Typography
            typography={"m2"}
            fontWeight={"fontWeightSemiBold"}
            color={"text.primary"}
          >
            Create agent scenarios
          </Typography>
          <Typography
            typography={"s1_2"}
            color={"text.secondary"}
            fontWeight={"fontWeightRegular"}
          >
            Use your agent definition to create scenarios and run tests
          </Typography>
        </Stack>
        <IconButton onClick={onClose}>
          <SvgColor
            src="/assets/icons/ic_close.svg"
            color="text.primary"
            sx={{
              width: "24px",
              height: "24px",
            }}
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: 0,
        }}
      >
        <Grid container spacing={2}>
          {stepsInfo.map((step, index) => (
            <Grid item xs={4} key={index}>
              <HelpCard
                title={step.title}
                description={step.description}
                imageSrc={step.imageSrc}
              />
            </Grid>
          ))}
        </Grid>
      </DialogContent>
      <DialogActions
        sx={{
          display: "flex",
          justifyContent: "center",
          padding: 0,
        }}
      >
        <Button
          onClick={handleCreateAgentScenarios}
          color="primary"
          variant="contained"
        >
          Create agent scenarios
        </Button>
      </DialogActions>
    </Dialog>
  );
}

CreateAgentScenarioHelp.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
