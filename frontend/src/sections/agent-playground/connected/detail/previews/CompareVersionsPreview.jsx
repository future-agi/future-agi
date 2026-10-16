import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { MONO } from "../format";

// Sample diff for the Compare preview, from the designer prototype.
const DIFF = [
  { type: "gap", text: "17 unchanged lines" },
  {
    type: "same",
    text: "- Keep turns short; confirm before any action that changes an account.",
  },
  {
    type: "same",
    text: "- If a request falls outside these rules, say so and offer a hand-over.",
  },
  { type: "add", text: "" },
  { type: "add", text: "## Additional rules" },
  {
    type: "add",
    text: "- Run check_compensation before offering any voucher or rebooking credit.",
  },
  { type: "del", text: "- Offer a voucher when the caller is unhappy." },
];

const MARK = { add: "+", del: "−", gap: "⋯", same: "" };

function FakeSelect({ label }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 1,
        height: 28,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO }}>
        {label}
      </Typography>
      <Iconify
        icon="eva:chevron-down-fill"
        width={16}
        sx={{ color: "text.subtitle" }}
      />
    </Stack>
  );
}

FakeSelect.propTypes = { label: PropTypes.string };

export default function CompareVersionsPreview() {
  return (
    <Box sx={{ minHeight: 360 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          Compare
        </Typography>
        <FakeSelect label="v1" />
        <Iconify
          icon="solar:arrow-right-linear"
          width={14}
          sx={{ color: "text.subtitle" }}
        />
        <FakeSelect label="v2" />
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          1 file changed
        </Typography>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Iconify
          icon="eva:chevron-down-fill"
          width={16}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ flex: 1, fontFamily: MONO, fontSize: 12.5 }}>
          prompts/system.md
        </Typography>
        <Typography
          sx={{ typography: "s3", fontWeight: 700, color: "text.secondary" }}
        >
          changed
        </Typography>
      </Stack>
      <Box sx={{ bgcolor: "background.neutral", py: 0.75 }}>
        {DIFF.map((line, i) => (
          <Stack
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            direction="row"
            sx={{
              bgcolor: (t) =>
                line.type === "add"
                  ? alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1)
                  : line.type === "del"
                    ? alpha("#DC2626", t.palette.mode === "dark" ? 0.16 : 0.08)
                    : "transparent",
            }}
          >
            <Typography
              component="span"
              sx={{
                width: 28,
                flexShrink: 0,
                textAlign: "center",
                color: "text.disabled",
                fontFamily: MONO,
                fontSize: 12,
                lineHeight: 1.7,
              }}
            >
              {MARK[line.type]}
            </Typography>
            <Typography
              component="span"
              sx={{
                fontFamily: MONO,
                fontSize: 12.5,
                lineHeight: 1.7,
                whiteSpace: "pre-wrap",
                pr: 2,
                color: line.type === "gap" ? "text.disabled" : "text.primary",
              }}
            >
              {line.text || " "}
            </Typography>
          </Stack>
        ))}
      </Box>
    </Box>
  );
}
