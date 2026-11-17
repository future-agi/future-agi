import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  InputAdornment,
  Stack,
  TextField,
  ToggleButton,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CodeLines from "src/sections/common/Values/CodeLines";
import { lineCountText } from "src/sections/common/Values/jsonLines";
import { rawFields } from "./rowModel";
import CopyButton, { copyText } from "src/sections/common/ux/CopyButton";
import { FieldTag } from "src/sections/common/ValueViewer/FieldCard";
import { fmtCount } from "src/sections/common/format/fmt";

const ROLE_LABEL = {
  input: "Input",
  expected: "Expected",
  output: "Output",
  metadata: "Metadata",
};

/**
 * The Row view's Raw tab (`rowTab=raw`, R2D-20 / RV#7): every field as
 * 2-space JSON with line numbers and syntax colours, a wrap toggle (wraps
 * only at spaces), a field filter (`/`) and `Copy row as JSON`.
 */
const RowRawTab = ({ model, rowJson }) => {
  const [wrap, setWrap] = useState(false);
  const [q, setQ] = useState("");
  const searchRef = useRef(null);
  const fields = useMemo(() => rawFields(model), [model]);
  const shown = q
    ? fields.filter((f) => f.name.toLowerCase().includes(q.toLowerCase()))
    : fields;

  return (
    <Box sx={{ minWidth: 0 }} data-testid="row-raw-tab">
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 2,
          px: 2,
          py: 0.875,
          bgcolor: "background.paper",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <TextField
          inputRef={searchRef}
          size="small"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              setQ("");
              searchRef.current?.blur();
            }
          }}
          placeholder="Find field…"
          inputProps={{ "aria-label": "Find field", "data-fa-search": "row" }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="eva:search-fill" width={16} />
              </InputAdornment>
            ),
          }}
          sx={{
            width: 200,
            "& .MuiInputBase-input": { typography: "s2", py: 0.5 },
          }}
        />
        <CustomTooltip
          show
          size="small"
          placement="bottom"
          arrow
          title={
            wrap
              ? "Wrapping long lines at spaces. Turn off to scroll sideways."
              : "Wrap long lines (breaks only at spaces)"
          }
        >
          <ToggleButton
            value="wrap"
            size="small"
            selected={wrap}
            onChange={() => setWrap((w) => !w)}
            aria-label="Wrap lines"
            sx={{
              py: 0.25,
              px: 1.25,
              typography: "s2",
              textTransform: "none",
              color: "text.secondary",
              borderColor: "divider",
              "&.Mui-selected": { color: "primary.main" },
            }}
          >
            <Iconify icon="mdi:wrap" width={15} sx={{ mr: 0.5 }} />
            Wrap
          </ToggleButton>
        </CustomTooltip>
        <Box sx={{ flex: 1 }} />
        <CopyRowJsonButton getText={rowJson} />
      </Stack>
      <Stack gap={1.5} sx={{ px: 2, pt: 1.5, pb: 3 }}>
        {shown.map((f) => (
          <Box
            key={f.id}
            data-testid={`raw-field-${f.name}`}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
              minWidth: 0,
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              sx={{ pl: 1.25, pr: 0.75, py: 0.5, minHeight: 34 }}
            >
              <Typography
                typography="s1"
                fontWeight="fontWeightMedium"
                sx={{
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                }}
                noWrap
              >
                {f.name}
              </Typography>
              {f.role && ROLE_LABEL[f.role] && (
                <FieldTag>{ROLE_LABEL[f.role]}</FieldTag>
              )}
              <Typography typography="s3" color="text.disabled">
                {lineCountText(f.text.split("\n").length)}
              </Typography>
              <Box sx={{ flex: 1 }} />
              <CopyButton value={f.text} tooltip={`Copy ${f.name} as JSON`} />
            </Stack>
            <Box sx={{ px: 0.75, pb: 0.75 }}>
              <CodeLines text={f.text} wrap={wrap} maxHeight={480} />
            </Box>
          </Box>
        ))}
        {shown.length === 0 && (
          <Stack alignItems="center" gap={1} sx={{ py: 4 }}>
            <Typography typography="s1" color="text.secondary">
              No field matches “{q}”.
            </Typography>
            <Button size="small" onClick={() => setQ("")}>
              Clear the filter
            </Button>
          </Stack>
        )}
        {q && shown.length > 0 && (
          <Typography typography="s3" color="text.disabled">
            Showing {fmtCount(shown.length, "field")} of {fields.length}
          </Typography>
        )}
      </Stack>
    </Box>
  );
};

RowRawTab.propTypes = {
  model: PropTypes.object.isRequired,
  rowJson: PropTypes.func.isRequired,
};

/**
 * `Copy row as JSON` with in-place feedback (RV#14): the icon turns into a
 * check and the label reads `Copied` for 1.5 s; no toast.
 */
export const CopyRowJsonButton = ({ getText }) => {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  return (
    <CustomTooltip
      show
      size="small"
      placement="bottom"
      arrow
      title={
        copied ? "Copied" : "Copy every field of this row as one JSON object"
      }
    >
      <Button
        size="small"
        color="inherit"
        data-testid="copy-row-json"
        onClick={async () => {
          if (!(await copyText(getText()))) return;
          setCopied(true);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => setCopied(false), 1500);
        }}
        startIcon={
          <Iconify
            icon={copied ? "mdi:check" : "tabler:copy"}
            width={15}
            sx={{ color: copied ? "green.600" : undefined }}
          />
        }
        sx={{ typography: "s2", color: "text.secondary", whiteSpace: "nowrap" }}
      >
        <span aria-live="polite">{copied ? "Copied" : "Copy row as JSON"}</span>
      </Button>
    </CustomTooltip>
  );
};

CopyRowJsonButton.propTypes = { getText: PropTypes.func.isRequired };

export default RowRawTab;
