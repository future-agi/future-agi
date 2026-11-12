// The HTTP card's response transform (W6 B2): declarative paths, no code.
// Output is the card's output path, or a template over several paths; tool
// calls and token usage are read from paths of the same response. The Test and
// every try of the run apply it (transformModel.applyTransform).
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  USAGE_FIELDS,
  transformIssues,
  transformOf,
  transformSummary,
} from "./transformModel";

const sel = { bgcolor: "background.paper", minWidth: 0 };

export default function ResponseTransform({ http, onChange }) {
  const t = http?.transform ?? {};
  const [open, setOpen] = useState(
    Boolean(t.output || t.tool_calls || Object.keys(t.usage ?? {}).length),
  );
  const set = (patch) => onChange({ ...t, ...patch });
  const effective = transformOf({
    output_path: http?.outputPath,
    transform: t,
  });
  const issues = transformIssues(effective);
  return (
    <Stack spacing={0.75} data-testid="http-transform">
      <Stack direction="row" gap={1} alignItems="center">
        <Typography typography="s2" fontWeight="fontWeightMedium">
          Response
        </Typography>
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="http-transform-summary"
        >
          {transformSummary(effective)}
        </Typography>
        <Button
          size="small"
          onClick={() => setOpen((v) => !v)}
          startIcon={
            <Iconify
              icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={14}
            />
          }
          data-testid="http-transform-toggle"
          sx={{ typography: "s2" }}
        >
          {open ? "Done" : "Edit"}
        </Button>
      </Stack>
      {open ? (
        <Stack spacing={0.75} sx={{ pl: 1 }}>
          <TextField
            size="small"
            label="Output template (optional)"
            placeholder="{{ $.reply }} (ref {{ $.ticket.id }})"
            value={t.output ?? ""}
            onChange={(e) => set({ output: e.target.value })}
            helperText="Empty: the output path above. A template joins several paths of the response."
            sx={sel}
            inputProps={{ "data-testid": "http-transform-output" }}
          />
          <TextField
            size="small"
            label="Tool calls from"
            placeholder="$.actions"
            value={t.tool_calls ?? ""}
            onChange={(e) => set({ tool_calls: e.target.value })}
            helperText="A list of {name, arguments} in the response: the trace shows each as a tool step."
            sx={sel}
            inputProps={{ "data-testid": "http-transform-tool-calls" }}
          />
          <Stack direction="row" gap={1}>
            {USAGE_FIELDS.map((f) => (
              <TextField
                key={f.id}
                size="small"
                label={f.label}
                placeholder={`$.usage.${f.id === "prompt_tokens" ? "input_tokens" : "output_tokens"}`}
                value={t.usage?.[f.id] ?? ""}
                onChange={(e) =>
                  set({ usage: { ...(t.usage ?? {}), [f.id]: e.target.value } })
                }
                sx={{ ...sel, flex: 1 }}
                inputProps={{ "data-testid": `http-transform-usage-${f.id}` }}
              />
            ))}
          </Stack>
          <Typography typography="s3" color="text.secondary">
            The tokens your endpoint reports become the try&apos;s token count
            in the results and its trace.
          </Typography>
          {issues.map((x) => (
            <Typography
              key={x}
              typography="s3"
              color="warning.dark"
              role="alert"
            >
              {x}
            </Typography>
          ))}
        </Stack>
      ) : null}
    </Stack>
  );
}

ResponseTransform.propTypes = {
  http: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
