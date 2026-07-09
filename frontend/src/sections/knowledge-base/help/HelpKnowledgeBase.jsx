import { Box, Button, Link, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useAuthContext } from "src/auth/hooks";
import { handleOnDocsClicked } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const HelpKnowledgeBase = ({ helpIcon = false, onCreateKnowledge }) => {
  const { role } = useAuthContext();

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: !helpIcon ? "center" : "flex-start",
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
            src="https://demo.arcade.software/x7sLrkNnZixgEQB9NsFE?embed&embed_mobile=inline&embed_desktop=inline&show_copy_link=true"
            title="Knowledge Base"
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
              colorScheme: "light",
            }}
          />
        </div>
      </Box>
      <Box maxWidth={helpIcon ? "100%" : "450px"}>
        {!helpIcon && (
          <Box sx={{ marginBottom: "20px" }}>
            <Typography
              // @ts-ignore
              variant="m1"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Steps to create knowledge base
            </Typography>
          </Box>
        )}
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
                sx={{ display: "flex", flexDirection: "column", gap: "2px" }}
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
            mt: helpIcon ? "24px" : "56px",
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
              width: helpIcon ? "max-content" : "100%",
              px: "24px",
              py: "8px",
              minWidth: "90px",
            }}
            disabled={!RolePermission.KNOWLEDGE_BASE[PERMISSIONS.CREATE][role]}
            onClick={() => {
              if (RolePermission.KNOWLEDGE_BASE[PERMISSIONS.CREATE][role]) {
                onCreateKnowledge();
              }
            }}
          >
            <Typography variant="m3" fontWeight={"fontWeightSemiBold"}>
              Create Knowledge Base
            </Typography>
          </Button>

          <Typography
            // @ts-ignore
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color="text.secondary"
          >
            For more instructions, check out our{" "}
            <Link
              // @ts-ignore
              target="_blank"
              variant="s1"
              fontWeight={"fontWeightSemiBold"}
              href="https://docs.futureagi.com/docs/knowledge-base"
              sx={{ textDecoration: "underline" }}
              onClick={() =>
                handleOnDocsClicked(
                  helpIcon
                    ? "knowledge_base_help_modal"
                    : "knowledge_base_page",
                )
              }
            >
              Docs
            </Link>
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

export default HelpKnowledgeBase;

HelpKnowledgeBase.propTypes = {
  helpIcon: PropTypes.bool,
  onCreateKnowledge: PropTypes.func,
};

const listItems = [
  {
    title: "Upload Documents",
    description:
      "Provide the necessary context to FAGI by uploading relevant documents to the system (UI or by SDK).",
  },
  {
    title: "Track Upload Status",
    description:
      "Monitor the progress of your document processing in real time.",
  },
  {
    title: "Access & Utilize Information",
    description:
      "Once processing of files is completed, reference the created knowledge base to generate synthetic data for training your AI agent.",
  },
];
