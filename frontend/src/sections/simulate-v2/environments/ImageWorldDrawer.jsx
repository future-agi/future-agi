import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import {
  IMAGE_CHECKS,
  IMAGE_RUNTIMES,
  PII_ACTIONS,
  SAMPLE_IMAGE,
  hasTagOnly,
  inspectionOf,
  isPinnedImage,
} from "../_mock/worldImage";

/*
  A world from your own image (T40): pin the image, inspect it in isolation,
  review what we found — tables, tools, personal data, relationships — then
  prove it resets and grades before it becomes a private release. One
  screen, each part opening as the one before it is done.
*/
const mono = { fontFamily: "ui-monospace, Menlo, monospace" };

const REVIEW_PARTS = [
  ["tables", "Tables"],
  ["tools", "Tools"],
  ["personal", "Personal data"],
  ["relations", "Relationships"],
];
/* "Tools, Personal data and Relationships" */
const listOf = (xs) =>
  xs.length < 2
    ? xs.join("")
    : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`;

function Part({ n, title, hint, done, children, action }) {
  return (
    <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}
      >
        <Typography
          sx={{
            typography: "s2",
            color: done ? "status.success.fg" : "text.tertiary",
            width: 16,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {done ? "✓" : n}
        </Typography>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {title}
          </Typography>
          {hint && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {hint}
            </Typography>
          )}
        </Box>
        {action}
      </Stack>
      <Box sx={{ px: 1.5, py: 1 }}>{children}</Box>
    </Box>
  );
}
Part.propTypes = {
  n: PropTypes.number,
  title: PropTypes.node,
  hint: PropTypes.node,
  done: PropTypes.bool,
  children: PropTypes.node,
  action: PropTypes.node,
};

const Line = ({ k, v, n }) => (
  <Stack direction="row" spacing={1.5} sx={{ py: 0.5 }}>
    <Typography sx={{ typography: "s2", ...mono, width: 210, flexShrink: 0 }}>
      {k}
    </Typography>
    <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1 }}>
      {v}
    </Typography>
    {n != null && (
      <Typography
        sx={{
          typography: "s2",
          color: "text.tertiary",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {n}
      </Typography>
    )}
  </Stack>
);
Line.propTypes = { k: PropTypes.node, v: PropTypes.node, n: PropTypes.node };

const Reviewed = ({ checked, onChange }) => (
  <FormControlLabel
    sx={{ mr: 0, "& .MuiFormControlLabel-label": { typography: "s2" } }}
    control={
      <Checkbox
        size="small"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
    }
    label="Looks right"
  />
);
Reviewed.propTypes = { checked: PropTypes.bool, onChange: PropTypes.func };

export default function ImageWorldDrawer({ open, onClose }) {
  const [image, setImage] = useState(SAMPLE_IMAGE);
  const [runtime, setRuntime] = useState(IMAGE_RUNTIMES[0].id);
  const [inspect, setInspect] = useState("idle");
  const [ok, setOk] = useState({});
  const [pii, setPii] = useState({});
  const [checks, setChecks] = useState("idle");
  const [published, setPublished] = useState(false);
  const found = inspect === "done" ? inspectionOf() : null;
  const pinned = isPinnedImage(image);
  /* What's still to review, by name, so the next step is never a guess. */
  const left = REVIEW_PARTS.filter(([k]) => !ok[k]).map(([, l]) => l);
  const reviewed = left.length === 0;
  const leftLine = `Tick Looks right on ${listOf(left)} first (${
    REVIEW_PARTS.length - left.length
  } of ${REVIEW_PARTS.length} reviewed).`;
  const run = (set, after) => {
    set("running");
    setTimeout(() => set(after), 1400);
  };
  const imageError = !image.trim()
    ? null
    : pinned
      ? null
      : hasTagOnly(image)
        ? "Pin it by digest (…@sha256:…). A tag can point at a different image tomorrow."
        : "An image reference, like ghcr.io/acme/app@sha256:…";

  return (
    <SideDrawer open={open} onClose={onClose} width={720}>
      <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.75,
            borderBottom: 1,
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
              World from your image
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              Supported runtimes only. We inspect it, you review it, and it
              becomes a private release once it resets and grades.
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Stack>

        <Stack
          spacing={1.5}
          sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2.5 }}
        >
          <Part
            n={1}
            title="The image"
            hint="Inspected in an isolated sandbox: no network, no secrets, a read-only copy."
            done={inspect === "done"}
            action={
              <Button
                size="small"
                variant="outlined"
                disabled={!pinned || inspect === "running"}
                onClick={() => run(setInspect, "done")}
                startIcon={<Iconify icon="solar:magnifer-linear" width={16} />}
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                {inspect === "done" ? "Inspect again" : "Inspect image"}
              </Button>
            }
          >
            <Stack spacing={1.25} sx={{ py: 0.5 }}>
              <TextField
                size="small"
                label="Image"
                value={image}
                onChange={(e) => {
                  setImage(e.target.value);
                  setInspect("idle");
                }}
                error={!!imageError}
                helperText={imageError || " "}
                InputProps={{ sx: { ...mono, typography: "s2" } }}
                fullWidth
              />
              <TextField
                select
                size="small"
                label="Runtime"
                value={runtime}
                onChange={(e) => setRuntime(e.target.value)}
                sx={{ width: 320 }}
              >
                {IMAGE_RUNTIMES.map((r) => (
                  <MenuItem key={r.id} value={r.id}>
                    {r.label}
                  </MenuItem>
                ))}
              </TextField>
              {inspect === "running" && (
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  Starting the image in isolation and reading its schema and
                  API…
                </Typography>
              )}
            </Stack>
          </Part>

          {found && (
            <>
              <Part
                n={2}
                title={`Tables (${found.tables.length})`}
                hint="The schema we read. Generated data fills these."
                done={!!ok.tables}
                action={
                  <Reviewed
                    checked={!!ok.tables}
                    onChange={(v) => setOk((o) => ({ ...o, tables: v }))}
                  />
                }
              >
                {found.tables.map((t) => (
                  <Line
                    key={t.name}
                    k={t.name}
                    v={`${t.columns} columns`}
                    n={`${t.rows.toLocaleString()} rows in the image`}
                  />
                ))}
              </Part>
              <Part
                n={3}
                title={`Tools (${found.tools.length})`}
                hint="What the API does to the world when the agent calls it."
                done={!!ok.tools}
                action={
                  <Reviewed
                    checked={!!ok.tools}
                    onChange={(v) => setOk((o) => ({ ...o, tools: v }))}
                  />
                }
              >
                {found.tools.map((t) => (
                  <Line key={t.name} k={t.name} v={t.effect} />
                ))}
              </Part>
              <Part
                n={4}
                title={`Personal data (${found.personal.length} columns)`}
                hint="None of the image's own values reach the world."
                done={!!ok.personal}
                action={
                  <Reviewed
                    checked={!!ok.personal}
                    onChange={(v) => setOk((o) => ({ ...o, personal: v }))}
                  />
                }
              >
                {found.personal.map((p) => (
                  <Stack
                    key={p.column}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    sx={{ py: 0.5 }}
                  >
                    <Typography
                      sx={{
                        typography: "s2",
                        ...mono,
                        width: 210,
                        flexShrink: 0,
                      }}
                    >
                      {p.column}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s2",
                        color: "text.secondary",
                        flex: 1,
                      }}
                    >
                      {p.kind}
                    </Typography>
                    <TextField
                      select
                      size="small"
                      value={pii[p.column] || "generate"}
                      onChange={(e) =>
                        setPii((x) => ({ ...x, [p.column]: e.target.value }))
                      }
                      sx={{ width: 240 }}
                    >
                      {PII_ACTIONS.map((a) => (
                        <MenuItem key={a.id} value={a.id}>
                          {a.label}
                        </MenuItem>
                      ))}
                    </TextField>
                  </Stack>
                ))}
              </Part>
              <Part
                n={5}
                title={`Relationships (${found.relations.length})`}
                hint="Generated data keeps these, so every order has its customer."
                done={!!ok.relations}
                action={
                  <Reviewed
                    checked={!!ok.relations}
                    onChange={(v) => setOk((o) => ({ ...o, relations: v }))}
                  />
                }
              >
                {found.relations.map((r) => (
                  <Line key={r.from} k={r.from} v={`→ ${r.to}`} />
                ))}
              </Part>
              <Part
                n={6}
                title="Prove it resets and grades"
                hint="All four must pass before it becomes a private release."
                done={checks === "passed"}
                action={
                  <Tooltip arrow title={reviewed ? "" : leftLine}>
                    <span>
                      <Button
                        size="small"
                        variant="outlined"
                        disabled={!reviewed || checks === "running"}
                        onClick={() => run(setChecks, "passed")}
                        startIcon={
                          <Iconify icon="solar:play-linear" width={16} />
                        }
                        sx={{ color: "text.primary", borderColor: "divider" }}
                      >
                        {checks === "passed" ? "Run again" : "Run checks"}
                      </Button>
                    </span>
                  </Tooltip>
                }
              >
                {IMAGE_CHECKS.map((c) => (
                  <Stack
                    key={c.id}
                    direction="row"
                    spacing={1.25}
                    sx={{ py: 0.5 }}
                  >
                    <Typography
                      sx={{
                        typography: "s2",
                        width: 64,
                        flexShrink: 0,
                        color:
                          checks === "passed"
                            ? "status.success.fg"
                            : "text.tertiary",
                      }}
                    >
                      {checks === "passed"
                        ? "Passed"
                        : checks === "running"
                          ? "Running"
                          : "Not run"}
                    </Typography>
                    <Typography sx={{ typography: "s2" }}>{c.label}</Typography>
                  </Stack>
                ))}
                {!reviewed && (
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary", pt: 0.75 }}
                  >
                    {leftLine}
                  </Typography>
                )}
              </Part>
            </>
          )}
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            pl: 2.5,
            pr: 9,
            py: 1.5,
            borderTop: 1,
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
          >
            {published
              ? "Published as a private release, visible to this workspace only."
              : checks === "passed"
                ? "Ready: it resets and grades. Only this workspace will see it."
                : found && !reviewed
                  ? `Next: ${leftLine.charAt(0).toLowerCase()}${leftLine.slice(1)}`
                  : found
                    ? "Next: run the checks."
                    : "Not every image can be inspected; unsupported ones are refused, not guessed at."}
          </Typography>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={checks !== "passed" || published}
            onClick={() => setPublished(true)}
          >
            {published ? "Published" : "Publish private release"}
          </Button>
        </Stack>
      </Box>
    </SideDrawer>
  );
}
ImageWorldDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
