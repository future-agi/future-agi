import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CodeBlock from "src/components/code-block";
import { ALK_CLOUD_ONLY, ALK_NETWORK, alkCommandsOf } from "../_mock/alkLocal";

/*
  The other place an agent from code can run: your machine, with ALK. Folded
  under Where it runs — hosted stays the default — and opened to one
  copy-paste block plus what it contacts.
*/
export default function RunWithAlk({ path, count }) {
  const [open, setOpen] = useState(false);
  return (
    <Box>
      <Button
        variant="outlined"
        size="small"
        color="inherit"
        onClick={() => setOpen((o) => !o)}
        startIcon={<Iconify icon="solar:laptop-2-linear" width={14} />}
        endIcon={
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={12}
          />
        }
      >
        Run it on your machine with ALK
      </Button>
      {open && (
        <Stack spacing={1} sx={{ mt: 1.25 }}>
          <CodeBlock code={alkCommandsOf({ path, count })} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {ALK_NETWORK}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {ALK_CLOUD_ONLY}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
RunWithAlk.propTypes = {
  /* The agent's folder on disk, for the harness's --path. */
  path: PropTypes.string,
  count: PropTypes.number,
};
