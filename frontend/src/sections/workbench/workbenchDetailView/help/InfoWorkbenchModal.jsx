import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogTitle,
  IconButton,
  Link,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { handleOnDocsClicked } from "src/utils/Mixpanel";

const InfoWorkbenchModal = ({ open, onClose }) => {
  const theme = useTheme();
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      PaperProps={{
        width: "660px",
        maxWidth: "none",
      }}
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box display="flex" justifyContent={"space-between"}>
            <Typography
              // @ts-ignore
              variant="m1"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Steps to use Workbench
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
        </DialogTitle>
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            gap: "12px 24px",
            flexWrap: "wrap",
          }}
        >
          <Box
            sx={{
              flex: 1,
              height: "100%",
              minHeight: "300px",
              minWidth: "450px",
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
              <iframe
                src="https://demo.arcade.software/mKO2mi1golHycxjTbofy?embed&embed_mobile=inline&embed_desktop=inline&show_copy_link=true"
                title="Prompt Workbench Demo"
                frameBorder="0"
                loading="lazy"
                allow="clipboard-write"
                allowFullScreen
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  colorScheme: theme.palette.mode,
                }}
              />
            </div>
          </Box>
          <Box width={"100%"}>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {listItems.map((step, index) => (
                <Box key={index} sx={{ display: "flex", gap: "12px" }}>
                  <Typography
                    // @ts-ignore
                    variant="m3"
                    sx={{
                      fontWeight: "fontWeightSemiBold",
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
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "2px",
                    }}
                  >
                    <Typography
                      // @ts-ignore
                      variant="m3"
                      sx={{
                        fontWeight: "fontWeightSemiBold",
                        color: "text.primary",
                      }}
                    >
                      {step.title}
                    </Typography>
                    <Typography
                      // @ts-ignore
                      variant="s1"
                      sx={{
                        fontWeight: "fontWeightRegular",
                        color: "text.disabled",
                      }}
                    >
                      {step.description}
                    </Typography>
                  </Box>
                </Box>
              ))}
            </Box>

            <Box
              sx={{
                mt: "16px",
                // textAlign: "center",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "12px",
              }}
            >
              <Button
                variant="contained"
                color="primary"
                sx={{
                  width: "max-content",
                  px: "24px",
                  py: "8px",
                  minWidth: "90px",
                }}
                onClick={onClose}
              >
                <Typography variant="m3" fontWeight={"fontWeightSemiBold"}>
                  Okay, got it
                </Typography>
              </Button>

              <Typography
                // @ts-ignore
                variant="s1"
                fontWeight={"fontWeightMedium"}
                color="text.secondary"
              >
                For more instructions, checkout our{" "}
                <Link
                  // @ts-ignore
                  target="_blank"
                  variant="s1"
                  fontWeight={"fontWeightSemiBold"}
                  href="https://docs.futureagi.com/docs/prompt"
                  sx={{ textDecoration: "underline" }}
                  onClick={() => handleOnDocsClicked("workbench_help_modal")}
                >
                  Docs
                </Link>
              </Typography>
            </Box>
          </Box>
        </Box>
      </Box>
    </Dialog>
  );
};

export default InfoWorkbenchModal;

InfoWorkbenchModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onCreateKnowledge: PropTypes.func,
};

const listItems = [
  {
    title: "Add prompt",
    description:
      "Write prompt and upload documents/ image/ audio along with variables",
  },
  {
    title: "Define variables",
    description: "Define or generate the variables defined in the prompt",
  },
  {
    title: "Run Prompt and evaluate your Prompt",
    description: "Access and Evaluate results quickly using FAGI evaluations",
  },
];
