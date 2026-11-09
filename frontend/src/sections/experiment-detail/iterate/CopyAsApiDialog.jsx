import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "src/components/snackbar";
import { copyToClipboard } from "src/utils/utils";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddRowDrawer/AddDatasetStyle";
import {
  fiRequest,
  requestSnippets,
} from "src/sections/common/commands/requestSnippets";
import { toIterateBody } from "src/api/develop/r2";
import { MONO } from "./changes/changeParts";

export const iteratePath = (experimentId) =>
  `/model-hub/experiments/v2/${experimentId}/runs/`;

/**
 * cURL, Python and TypeScript for the I1 request the dock sends, built by
 * the one command builder: the body goes through the same `toIterateBody`
 * as `useIterate`, so the printed body is the sent body. Pure.
 */
export function buildApiSnippets(experimentId, body) {
  return requestSnippets(
    fiRequest(iteratePath(experimentId), toIterateBody(body ?? {})),
  );
}

const TABS = [
  { value: "curl", label: "cURL" },
  { value: "python", label: "Python" },
  { value: "typescript", label: "TypeScript" },
];

/** `More ▾ › Copy as API call`: the I1 request the dock would send. */
const CopyAsApiDialog = ({ open, onClose, experimentId, body, runLabel }) => {
  const theme = useTheme();
  const [tab, setTab] = useState("curl");
  const snippets = useMemo(
    () => buildApiSnippets(experimentId, body),
    [experimentId, body],
  );
  const code = snippets[tab];
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography
            typography="m3"
            fontWeight="fontWeightSemiBold"
            sx={{ flex: 1 }}
          >
            {`Test a fix on ${runLabel} as an API call (REST)`}
          </Typography>
          <IconButton aria-label="Close" onClick={onClose} size="small">
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1}>
          <TabWrapper sx={{ mb: 0, alignSelf: "flex-start" }}>
            <CustomTabs
              textColor="primary"
              value={tab}
              onChange={(e, v) => setTab(v)}
              TabIndicatorProps={{
                style: {
                  backgroundColor: theme.palette.primary.main,
                  opacity: 0.08,
                  height: "100%",
                  borderRadius: "8px",
                },
              }}
            >
              {TABS.map((t) => (
                <CustomTab key={t.value} label={t.label} value={t.value} />
              ))}
            </CustomTabs>
          </TabWrapper>
          <Box
            sx={{
              position: "relative",
              bgcolor: "background.neutral",
              borderRadius: "8px",
              border: "1px solid var(--border-default)",
            }}
          >
            <CustomTooltip show title="Copy" size="small" type="black">
              <IconButton
                size="small"
                aria-label={`Copy ${TABS.find((t) => t.value === tab)?.label ?? tab}`}
                onClick={() => {
                  copyToClipboard(code);
                  enqueueSnackbar("Copied", { variant: "success" });
                }}
                sx={{ position: "absolute", top: 6, right: 6 }}
              >
                <Iconify icon="eva:copy-outline" width={16} />
              </IconButton>
            </CustomTooltip>
            <Box
              component="pre"
              data-testid="iterate-api-snippet"
              sx={{
                m: 0,
                p: 1.5,
                pr: 5,
                maxHeight: 420,
                overflow: "auto",
                fontFamily: MONO,
                fontSize: 12,
                lineHeight: 1.55,
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              {code}
            </Box>
          </Box>
          <Typography typography="s3" color="text.secondary">
            Set FI_API_KEY and FI_SECRET_KEY from Settings → Keys. The body is
            the one Run sends; the response has the new run and its compare
            link.
          </Typography>
        </Stack>
      </DialogContent>
    </Dialog>
  );
};

CopyAsApiDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  experimentId: PropTypes.string,
  body: PropTypes.object.isRequired,
  runLabel: PropTypes.string,
};

export default CopyAsApiDialog;
