import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import CopyableCommand from "./CopyableCommand";
import StudioIcon from "./StudioIcon";
import StudioInstallStep from "./StudioInstallStep";
import { cliInstallCommand, studioDocsUrl } from "./release-config";
import { START, omegaDevCommand } from "./studio-copy";

function Step({ number, children }) {
  return (
    <Box sx={{ display: "flex", gap: 1.5, minWidth: 0 }}>
      <Box
        aria-hidden
        sx={{
          width: 22,
          height: 22,
          mt: "1px",
          flexShrink: 0,
          borderRadius: "50%",
          border: "1px solid",
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          typography: "s3",
          fontWeight: 600,
          color: "text.secondary",
        }}
      >
        {number}
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Box>
  );
}

Step.propTypes = { number: PropTypes.number, children: PropTypes.node };

// How to start Studio so it opens here: install the Omega CLI (only once the
// release names its package: no step otherwise), run the exact command for
// this site's origin in an Omega project, then connect with the link it
// prints. The Studio guide is linked only once the release sets its address.
export default function StartOmegaDev({ origin }) {
  const install = cliInstallCommand();
  const docs = studioDocsUrl();
  const first = install ? 2 : 1;
  return (
    <Box data-testid="studio-start" component="section">
      <Typography
        component="h2"
        variant="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
        sx={{ mb: 1.5 }}
      >
        {START.title}
      </Typography>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {install && (
          <Step number={1}>
            <StudioInstallStep command={install} />
          </Step>
        )}
        <Step number={first}>
          <Typography
            variant="s1"
            color="text.secondary"
            component="p"
            sx={{ mb: 1 }}
          >
            {START.stepRun}
          </Typography>
          <CopyableCommand
            command={omegaDevCommand(origin)}
            testId="studio-start-command"
          />
          <Typography
            variant="s2"
            color="text.disabled"
            component="p"
            sx={{ mt: 0.5 }}
            data-testid="studio-start-project"
          >
            {START.project}
          </Typography>
        </Step>
        <Step number={first + 1}>
          <Typography
            variant="s1"
            color="text.secondary"
            component="p"
            data-testid="studio-start-open"
          >
            {START.stepOpen}
          </Typography>
          <Typography
            variant="s2"
            color="text.disabled"
            component="p"
            sx={{ mt: 0.5 }}
            data-testid="studio-start-signin"
          >
            {START.stepSignIn}
          </Typography>
        </Step>
      </Box>
      {docs && (
        <Link
          href={docs}
          target="_blank"
          rel="noopener noreferrer"
          underline="hover"
          data-testid="studio-docs-link"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            mt: 2,
            typography: "s1",
          }}
        >
          {START.docsLink}
          <StudioIcon name="external" size={13} />
        </Link>
      )}
      <Typography
        variant="s2"
        color="text.secondary"
        component="p"
        sx={{ mt: 2.5, pt: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        {START.privacy}
      </Typography>
    </Box>
  );
}

StartOmegaDev.propTypes = {
  origin: PropTypes.string.isRequired,
};
