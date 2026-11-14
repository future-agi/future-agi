import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Dialog,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import DescriptionSummary from "./Summary/DescriptionSummary";
import ObjectiveSummary from "./Summary/ObjectiveSummary";
import WrittingPatternSummary from "./Summary/WrittingPatternSummary";
import KnowledgeBaseSummary from "./Summary/KnowledgeBaseSummary";

const HelpCreateSyntheticData = ({ open, onClose }) => {
  const [expanded, setExpanded] = useState(false);

  const handleChange = (panel) => (event, isExpanded) => {
    setExpanded(isExpanded ? panel : false);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
    >
      <Box
        sx={{
          padding: 2,
          overflowY: "auto",
          scrollbarWidth: "none",
          msOverflowStyle: "none",
          "&::-webkit-scrollbar": {
            display: "none",
          },
        }}
      >
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box
            paddingBottom={3}
            display="flex"
            justifyContent={"space-between"}
          >
            <Typography
              // @ts-ignore
              variant="m1"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Steps to create Synthetic Data
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
        </DialogTitle>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: "12px",
            flexWrap: "wrap",
            flexDirection: "column",
          }}
        >
          <Box
            sx={{
              flex: 1,
              border: "1px solid",
              borderColor: "text.primary",
              borderRadius: "8px",
              height: "100%",
              minHeight: "200px",
              minWidth: "400px",
            }}
          >
            <div
              style={{
                position: "relative",
                paddingBottom: "calc(66.66666666666666% + 41px)",
                height: 0,
                width: "100%",
              }}
            >
              <iframe
                src="https://demo.arcade.software/KkkN0EJhy0NnLNTITrQZ?embed&embed_mobile=inline&embed_desktop=inline&show_copy_link=true"
                title="Arcade Flow (Wed Apr 30 2025)"
                frameBorder="0"
                loading="lazy"
                allowFullScreen
                allow="clipboard-write"
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  colorScheme: "light",
                }}
              />
            </div>
          </Box>
          <Box sx={{ flex: 1, maxWidth: "400px" }}>
            <Box sx={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {listItems.map((step, index) => {
                const panelId = `panel-${index}`;
                return (
                  <Accordion
                    key={index}
                    // @ts-ignore
                    expanded={expanded === panelId}
                    onChange={handleChange(panelId)}
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      margin: "0px !important",
                    }}
                  >
                    <AccordionSummary
                      expandIcon={
                        <Iconify
                          icon="ooui:expand"
                          width="16px"
                          height="16px"
                          color="text.primary"
                        />
                      }
                      sx={{
                        position: "relative",
                        minHeight: "20px !important",
                        "& .Mui-expanded": {
                          m: "16px 16px 12px 0px",
                        },
                        "& .MuiAccordionSummary-expandIconWrapper": {
                          marginRight: "10px",
                        },
                      }}
                      aria-controls={step.title}
                      id={step.title}
                    >
                      <Typography
                        variant="s2"
                        fontWeight="fontWeightMedium"
                        color="text.primary"
                      >
                        {step.title}
                      </Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ marginTop: 0, paddingTop: 0 }}>
                      {step.description}
                    </AccordionDetails>
                  </Accordion>
                );
              })}
            </Box>

            <Box
              sx={{
                mt: "24px",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "12px",
              }}
            >
              <Button
                variant="contained"
                color="primary"
                sx={{ width: "max-content" }}
                type="button"
                onClick={onClose}
              >
                Okay, got it
              </Button>
            </Box>
          </Box>
        </Box>
      </Box>
    </Dialog>
  );
};

export default HelpCreateSyntheticData;

HelpCreateSyntheticData.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

const listItems = [
  {
    title: "Why Knowledge Base is important?",
    description: <KnowledgeBaseSummary />,
  },
  {
    title: "How to write effective description?",
    description: <DescriptionSummary />,
  },
  {
    title: "Why Objective is important?",
    description: <ObjectiveSummary />,
  },
  {
    title: "Why Pattern is important?",
    description: <WrittingPatternSummary />,
  },
];
