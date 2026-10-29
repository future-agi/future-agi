import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, Button, LinearProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { readFile, roleLabel, SUPPORTED_LINE } from "../../_mock/orgData";

/* A file whose name says so fails its first upload — so Retry has something to do. */
const failsFirst = (f) => /fail|flaky/i.test(f.name);

const STATUS = {
  uploading: { label: "Uploading", tone: "text.secondary" },
  parsing: { label: "Reading", tone: "text.secondary" },
  ready: { label: "Ready", tone: "text.secondary" },
  "needs-attention": { label: "Needs attention", tone: "status.warning.fg" },
  unreadable: { label: "Can't read", tone: "status.danger.fg" },
  unsupported: { label: "Not supported", tone: "status.danger.fg" },
  failed: { label: "Upload failed", tone: "status.danger.fg" },
  cancelled: { label: "Cancelled", tone: "text.tertiary" },
};

/**
 * Files attached in builder chat (roadmap data.md §1): each uploads on its
 * own, with progress, Cancel and Retry; one failing doesn't drop the others
 * or the message. Once every file has settled, the readable ones are handed
 * on (`onDone`) and the builder proposes what to do with them.
 */
export default function AttachmentsCard({ files, onDone }) {
  const [items, setItems] = useState(() =>
    files.map((f, i) => ({
      key: `${f.name}-${i}`,
      file: f,
      progress: 0,
      status: "uploading",
      tries: 0,
      source: null,
    })),
  );
  const done = useRef(false);

  useEffect(() => {
    const t = setInterval(() => {
      setItems((cur) =>
        cur.map((it) => {
          if (it.status === "uploading") {
            const p = Math.min(100, it.progress + 18 + (it.key.length % 7));
            if (p >= 60 && failsFirst(it.file) && it.tries === 0)
              return { ...it, progress: p, status: "failed", tries: 1 };
            return p >= 100
              ? { ...it, progress: 100, status: "parsing" }
              : { ...it, progress: p };
          }
          if (it.status === "parsing") {
            const source = readFile(it.file);
            return { ...it, source, status: source.status };
          }
          return it;
        }),
      );
    }, 280);
    return () => clearInterval(t);
  }, []);

  const settled = items.every(
    (it) => !["uploading", "parsing"].includes(it.status),
  );
  useEffect(() => {
    if (!settled || done.current) return;
    if (items.some((it) => it.status === "failed")) return;
    done.current = true;
    onDone(items.filter((it) => it.source).map((it) => it.source));
  }, [settled, items, onDone]);

  const set = (key, fields) =>
    setItems((cur) =>
      cur.map((it) => (it.key === key ? { ...it, ...fields } : it)),
    );

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      {items.map((it) => {
        const s = STATUS[it.status];
        const busy = it.status === "uploading" || it.status === "parsing";
        return (
          <Box
            key={it.key}
            sx={{
              px: 1.25,
              py: 1,
              "& + &": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1}>
              <Iconify
                icon="solar:file-text-linear"
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Typography
                noWrap
                sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
              >
                {it.file.name}
              </Typography>
              <Typography
                sx={{ typography: "s3", color: s.tone, flexShrink: 0 }}
              >
                {it.status === "uploading"
                  ? `${s.label} ${it.progress}%`
                  : s.label}
              </Typography>
              {it.status === "uploading" && (
                <Button
                  size="small"
                  onClick={() => set(it.key, { status: "cancelled" })}
                  sx={{ minWidth: 0, color: "text.secondary" }}
                >
                  Cancel
                </Button>
              )}
              {(it.status === "failed" || it.status === "cancelled") && (
                <Button
                  size="small"
                  onClick={() =>
                    set(it.key, { status: "uploading", progress: 0 })
                  }
                  sx={{ minWidth: 0, color: "text.secondary" }}
                >
                  {it.status === "failed" ? "Retry" : "Attach again"}
                </Button>
              )}
            </Stack>
            {busy && (
              <LinearProgress
                variant={
                  it.status === "parsing" ? "indeterminate" : "determinate"
                }
                value={it.progress}
                sx={{ mt: 0.75, height: 2 }}
              />
            )}
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {it.status === "failed"
                ? "The connection dropped at 60% — the others are kept. Retry this one."
                : it.status === "unsupported"
                  ? `Kept here so you know — ${SUPPORTED_LINE}.`
                  : it.source
                    ? [
                        it.source.role && roleLabel(it.source.role),
                        it.source.parsed?.kind === "table" &&
                          `${it.source.parsed.accepted.toLocaleString()} of ${it.source.parsed.rows.toLocaleString()} rows`,
                        it.source.parsed?.kind === "text" &&
                          `${it.source.parsed.passages.length} passages`,
                        it.source.statusNote,
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : `${Math.max(1, Math.round(it.file.size / 1024))} kB`}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}

AttachmentsCard.propTypes = {
  files: PropTypes.array.isRequired,
  onDone: PropTypes.func.isRequired,
};
