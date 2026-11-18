import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { SUPPORTED_ACCEPT, readFile, roleLabel } from "../_mock/orgData";
import { readDesignFiles } from "../_mock/designSources";

/*
  Files for the environment, asked wherever one is built from an agent — the
  same as Build from scratch (builder.md §1, data.md "Choose a source"). The
  agent's code says what it can do; it doesn't hold your refund policy or
  what your orders look like. A policy becomes cited rules and a CSV the
  world's records — reviewed in the design before anything is built.

  A drop area like the upload flow's, and each file says how it will be read,
  so what happens to it is clear before the build.
*/
const kb = (n) =>
  n >= 1024 * 1024
    ? `${(n / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(n / 1024))} KB`;

const extOf = (name) =>
  String(name || "")
    .split(".")
    .pop()
    .toUpperCase();

export default function BuildFilesField({ files, onChange }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const add = async (list) => {
    if (!list?.length) return;
    /* Text files are read for their words; the rest travel by name and size. */
    const read = (await readDesignFiles(Array.from(list))).map((f) => ({
      id: `${f.name}-${f.size}`,
      name: f.name,
      size: f.size || 0,
      type: f.type || "",
      ...(typeof f.text === "string" && { text: f.text }),
    }));
    /* The same name again replaces the earlier copy — it's its newer version. */
    onChange([
      ...files.filter((f) => !read.some((r) => r.name === f.name)),
      ...read,
    ]);
  };
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
        Files and data
        <Box
          component="span"
          sx={{ color: "text.tertiary", fontWeight: 400, ml: 0.75 }}
        >
          optional
        </Box>
      </Typography>
      <Box
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (e.dataTransfer?.files?.length) add(e.dataTransfer.files);
        }}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          px: 1.75,
          py: 1.5,
          border: "1px dashed",
          borderColor: over ? "text.primary" : "divider",
          borderRadius: 1,
          bgcolor: (t) =>
            over ? alpha(t.palette.text.primary, 0.05) : "transparent",
          transition: "background-color .12s ease, border-color .12s ease",
        }}
      >
        <Iconify
          icon="solar:cloud-upload-linear"
          width={20}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {over
              ? "Drop to add"
              : "Drop a policy, a CSV of records or notes here"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            PDF, Word, Excel, CSV, JSON or text · up to 25 MB
          </Typography>
        </Box>
        <input
          ref={input}
          type="file"
          hidden
          multiple
          accept={SUPPORTED_ACCEPT}
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={() => input.current?.click()}
          startIcon={
            <Iconify icon="solar:upload-minimalistic-linear" width={14} />
          }
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Choose files
        </Button>
      </Box>
      {files.length > 0 && (
        <Box
          sx={{
            mt: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {files.map((f) => {
            /* How the builder will read it — changeable in the design review. */
            const role = readFile(f).role;
            return (
              <Stack
                key={f.id}
                direction="row"
                alignItems="center"
                spacing={1.25}
                sx={{ px: 1.5, minHeight: 40 }}
              >
                <Box
                  sx={{
                    px: 0.75,
                    height: 20,
                    display: "grid",
                    placeItems: "center",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "4px",
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.secondary",
                    fontFamily: "ui-monospace, Menlo, monospace",
                    flexShrink: 0,
                  }}
                >
                  {extOf(f.name)}
                </Box>
                <Typography
                  noWrap
                  sx={{ typography: "s1", fontWeight: 500, minWidth: 0 }}
                >
                  {f.name}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {role ? `Read as ${roleLabel(role)}` : "Can't be read"}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontVariantNumeric: "tabular-nums",
                    flexShrink: 0,
                  }}
                >
                  {kb(f.size)}
                </Typography>
                <IconButton
                  size="small"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => onChange(files.filter((x) => x.id !== f.id))}
                  sx={{ color: "text.tertiary", p: 0.25, flexShrink: 0 }}
                >
                  <Iconify icon="solar:close-circle-linear" width={16} />
                </IconButton>
              </Stack>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

BuildFilesField.propTypes = {
  /* [{ id, name, size, type, text? }] — plain objects, so they can travel
     with the build in navigation state. */
  files: PropTypes.arrayOf(PropTypes.object).isRequired,
  onChange: PropTypes.func.isRequired,
};
