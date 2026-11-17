import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";

import CodeIcon from "./CodeIcon";
import CodePanel from "./CodePanel";

/**
 * A page's "Code" action: the calls this page makes, as commands a program
 * can run against this same stack with a personal API key. Shown only when
 * the page has at least one call to offer. `note` says why a call the page
 * shows elsewhere is not listed for this person (a sandbox's creator-only
 * calls).
 */
export default function CodeButton({ requests, subject, note = null }) {
  const [open, setOpen] = useState(false);
  if (!requests || requests.length === 0) return null;
  return (
    <>
      <Button
        size="small"
        color="inherit"
        variant="outlined"
        onClick={() => setOpen(true)}
        startIcon={<CodeIcon name="code" />}
        data-testid="code-open"
      >
        Code
      </Button>
      <Drawer
        anchor="right"
        open={open}
        onClose={() => setOpen(false)}
        PaperProps={{
          sx: { width: { xs: "100%", sm: 640 }, bgcolor: "background.paper" },
        }}
      >
        <Box
          data-testid="code-drawer"
          sx={{ height: "100%", display: "flex", flexDirection: "column" }}
        >
          <Box
            sx={{
              px: 2.5,
              py: 1.5,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography
                typography="m3"
                fontWeight="fontWeightMedium"
                color="text.primary"
              >
                Call {subject} from code
              </Typography>
              <Typography typography="s2" color="text.secondary">
                The same requests this page sends, to this stack, with your API
                key.
              </Typography>
            </Box>
            <IconButton
              aria-label="Close"
              size="small"
              onClick={() => setOpen(false)}
              data-testid="code-close"
            >
              <CodeIcon name="close" size={18} />
            </IconButton>
          </Box>
          <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
            {note && (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="code-note"
                sx={{ mb: 2 }}
              >
                {note}
              </Typography>
            )}
            {open && <CodePanel requests={requests} />}
          </Box>
        </Box>
      </Drawer>
    </>
  );
}

CodeButton.propTypes = {
  requests: PropTypes.array,
  subject: PropTypes.string.isRequired,
  note: PropTypes.string,
};
