import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import { kindDiffLabel } from "./configDiff";
import { captureLabel, provenanceOf } from "./provenanceModel";

const names = (list) => (list ?? []).map((x) => x.name ?? x.id).join(", ");
const mcpText = (m) =>
  (m ?? [])
    .map((x) => `${x.name}${x.tools != null ? ` (${x.tools} tools)` : ""}`)
    .join(", ");

/** R4 axes: the provenance facts behind one chip, side by side. */
function r4Rows(axis, pa, pb, ca, cb) {
  switch (axis) {
    case "framework":
      return [
        ["framework", pa.source.name, pb.source.name],
        ["version", ca.framework?.version, cb.framework?.version],
      ];
    case "models":
      return [
        [
          "models",
          pa.models.map((m) => `${m.id} · ${m.role}`).join(", "),
          pb.models.map((m) => `${m.id} · ${m.role}`).join(", "),
        ],
      ];
    case "mcp":
      return [["MCP servers", mcpText(pa.mcp), mcpText(pb.mcp)]];
    case "permissions":
      return [
        [
          "permissions",
          pa.harness?.permissionsLabel,
          pb.harness?.permissionsLabel,
        ],
      ];
    case "capture":
      return [["capture", captureLabel(pa), captureLabel(pb)]];
    case "safety":
      return [
        ["side effects", ca.safety?.side_effects, cb.safety?.side_effects],
        [
          "tools with side effects",
          pa.safety.sideEffectTools.join(", "),
          pb.safety.sideEffectTools.join(", "),
        ],
      ];
    case "execution":
      return [
        ["runs on", pa.where.label, pb.where.label],
        ["owner", pa.owner?.name, pb.owner?.name],
        ["runner", pa.where.runner, pb.where.runner],
        ["image", pa.where.image, pb.where.image],
        ["network", pa.where.network, pb.where.network],
        ["at a time", pa.where.concurrency, pb.where.concurrency],
      ];
    case "voice":
      return [
        ["provider", pa.voice?.providerName, pb.voice?.providerName],
        ["depth", pa.voice?.depth, pb.voice?.depth],
        ["STT", pa.voice?.stt?.model, pb.voice?.stt?.model],
        ["LLM", pa.voice?.llm?.model, pb.voice?.llm?.model],
        [
          "TTS",
          [pa.voice?.tts?.provider, pa.voice?.tts?.voice]
            .filter(Boolean)
            .join(" "),
          [pb.voice?.tts?.provider, pb.voice?.tts?.voice]
            .filter(Boolean)
            .join(" "),
        ],
        ["turn detection", pa.voice?.turn_detection, pb.voice?.turn_detection],
      ];
    case "endpoint":
      return [
        ["protocol", pa.endpoint?.protocol, pb.endpoint?.protocol],
        ["URL", pa.endpoint?.url, pb.endpoint?.url],
      ];
    default:
      return null;
  }
}

const fmt = (v) =>
  v === undefined || v === null || v === ""
    ? "—"
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);

function Section({ title, children, right }) {
  return (
    <Box sx={{ "& + &": { mt: 1.5 } }}>
      <Stack direction="row" alignItems="center" sx={{ mb: 0.5 }}>
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ flex: 1, textTransform: "uppercase", letterSpacing: 0.4 }}
        >
          {title}
        </Typography>
        {right}
      </Stack>
      {children}
    </Box>
  );
}

Section.propTypes = {
  title: PropTypes.node,
  children: PropTypes.node,
  right: PropTypes.node,
};

function CopyButton({ text, what }) {
  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      arrow
      title={`Copy ${what}`}
    >
      <IconButton
        size="small"
        aria-label={`Copy ${what}`}
        onClick={() => {
          copyToClipboard(text ?? "");
          enqueueSnackbar(
            `${what.charAt(0).toUpperCase()}${what.slice(1)} copied`,
            { variant: "success" },
          );
        }}
        sx={{ p: 0.25, color: "text.secondary" }}
      >
        <Iconify icon="basil:copy-outline" width={14} />
      </IconButton>
    </CustomTooltip>
  );
}

CopyButton.propTypes = { text: PropTypes.string, what: PropTypes.string };

/** Two-column key / value table: `key · a · b`, changed rows in bold. */
export function TwoColumns({ rows, labelA, labelB }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "minmax(110px, 0.8fr) minmax(0,1fr) minmax(0,1fr)",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        overflow: "hidden",
      }}
    >
      {["", labelA, labelB].map((h, i) => (
        <Typography
          key={`h${i}`}
          typography="s3"
          color="text.secondary"
          sx={{ px: 1, py: 0.5, bgcolor: "background.neutral" }}
        >
          {h}
        </Typography>
      ))}
      {rows.map(([k, a, b]) => {
        const changed = fmt(a) !== fmt(b);
        return (
          <React.Fragment key={k}>
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{
                px: 1,
                py: 0.5,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              {k}
            </Typography>
            {[a, b].map((v, i) => (
              <Typography
                key={i}
                typography="s2"
                fontWeight={changed ? "fontWeightMedium" : undefined}
                color={changed ? "text.primary" : "text.secondary"}
                sx={{
                  px: 1,
                  py: 0.5,
                  borderTop: "1px solid",
                  borderColor: "divider",
                  wordBreak: "break-word",
                }}
              >
                {fmt(v)}
              </Typography>
            ))}
          </React.Fragment>
        );
      })}
    </Box>
  );
}

TwoColumns.propTypes = {
  rows: PropTypes.array,
  labelA: PropTypes.string,
  labelB: PropTypes.string,
};

const msgText = (messages, role) =>
  (messages ?? [])
    .filter((m) => m.role === role)
    .map((m) =>
      typeof m.content === "string" ? m.content : JSON.stringify(m.content),
    )
    .join("\n\n");

function PromptDiff({ a, b }) {
  const roles = [
    ...new Set(
      [...(a?.messages ?? []), ...(b?.messages ?? [])].map((m) => m.role),
    ),
  ];
  if (!roles.length)
    return (
      <Typography typography="s2" color="text.secondary">
        {`${a?.version ?? "—"} → ${b?.version ?? "—"} (the message text was not captured)`}
      </Typography>
    );
  return roles.map((role) => {
    const ta = msgText(a?.messages, role);
    const tb = msgText(b?.messages, role);
    return (
      <Section
        key={role}
        title={`${role} message · ${a?.version ?? "—"} → ${b?.version ?? "—"}`}
        right={
          <Stack direction="row" alignItems="center" gap={0.25}>
            <Typography typography="s3" color="text.secondary">
              {a?.version ?? "A"}
            </Typography>
            <CopyButton
              text={ta}
              what={`${a?.version ?? "left"} ${role} message`}
            />
            <Typography typography="s3" color="text.secondary" sx={{ ml: 0.5 }}>
              {b?.version ?? "B"}
            </Typography>
            <CopyButton
              text={tb}
              what={`${b?.version ?? "right"} ${role} message`}
            />
          </Stack>
        }
      >
        {ta === tb ? (
          <Typography typography="s2" color="text.secondary">
            Unchanged
          </Typography>
        ) : (
          <TextDiff from={ta} to={tb} kind="words" />
        )}
      </Section>
    );
  });
}

PromptDiff.propTypes = { a: PropTypes.object, b: PropTypes.object };

function ToolsDiff({ a, b }) {
  const ta = a ?? [];
  const tb = b ?? [];
  const names = [...new Set([...ta, ...tb].map((t) => t.name))];
  return (
    <Stack spacing={0.75}>
      {names.map((name) => {
        const x = ta.find((t) => t.name === name);
        const y = tb.find((t) => t.name === name);
        const line = !x
          ? `+ ${name} (added)`
          : !y
            ? `− ${name} (removed)`
            : x.mode !== y.mode
              ? `${name} ${x.mode ?? "—"} → ${y.mode ?? "—"}`
              : x.schema_hash !== y.schema_hash ||
                  JSON.stringify(x.schema) !== JSON.stringify(y.schema)
                ? `${name} schema changed`
                : `${name} unchanged`;
        const schemaDiff =
          x &&
          y &&
          x.schema &&
          y.schema &&
          JSON.stringify(x.schema) !== JSON.stringify(y.schema);
        return (
          <Box key={name}>
            <Typography
              typography="s2"
              color={
                line.endsWith("unchanged") ? "text.secondary" : "text.primary"
              }
              sx={{
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              }}
            >
              {line}
            </Typography>
            {schemaDiff && (
              <TextDiff from={x.schema} to={y.schema} kind="json" />
            )}
          </Box>
        );
      })}
    </Stack>
  );
}

ToolsDiff.propTypes = { a: PropTypes.array, b: PropTypes.array };

function AgentDiff({ a, b }) {
  const na = a?.nodes ?? [];
  const nb = b?.nodes ?? [];
  const ids = [...new Set([...na, ...nb].map((n) => n.id))];
  const graphId = b?.graph_id ?? a?.graph_id;
  return (
    <Stack spacing={0.75}>
      {ids.map((id) => {
        const x = na.find((n) => n.id === id);
        const y = nb.find((n) => n.id === id);
        const changed = JSON.stringify(x ?? null) !== JSON.stringify(y ?? null);
        return (
          <Box key={id}>
            <Typography
              typography="s2"
              fontWeight={changed ? "fontWeightMedium" : undefined}
              color={changed ? "text.primary" : "text.secondary"}
              sx={{
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              }}
            >
              {`${!x ? "+ " : !y ? "− " : changed ? "~ " : ""}${id}${y?.model || x?.model ? ` · ${x?.model ?? "—"}${x?.model !== y?.model ? ` → ${y?.model ?? "—"}` : ""}` : ""}`}
            </Typography>
            {changed && x?.prompt && y?.prompt && x.prompt !== y.prompt && (
              <Box sx={{ pl: 2, pt: 0.5 }}>
                <TextDiff from={x.prompt} to={y.prompt} kind="words" />
              </Box>
            )}
          </Box>
        );
      })}
      {graphId && (
        <Link
          component="button"
          typography="s2"
          onClick={() =>
            window.open(
              `/dashboard/agents/playground/${graphId}/build`,
              "_blank",
              "noopener",
            )
          }
          sx={{ alignSelf: "flex-start" }}
        >
          {`Open ${String(b?.version ?? "").replace(/\s*\(.*\)$/, "") || "this version"} in Agent Playground ↗`}
        </Link>
      )}
    </Stack>
  );
}

AgentDiff.propTypes = { a: PropTypes.object, b: PropTypes.object };

function CodeDiff({ a, b }) {
  const [show, setShow] = useState(false);
  const files = b?.dirty?.files ?? [];
  return (
    <Stack spacing={0.75}>
      <Typography
        typography="s2"
        sx={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
      >
        {`${a?.commit ?? "—"} → ${b?.commit ?? "—"}`}
      </Typography>
      {files.map((f) => (
        <Typography
          key={f.path}
          typography="s2"
          sx={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
        >
          {f.path}{" "}
          <Box
            component="span"
            sx={{ color: "success.main" }}
          >{`+${f.plus}`}</Box>{" "}
          <Box
            component="span"
            sx={{ color: "error.main" }}
          >{`−${f.minus}`}</Box>
        </Typography>
      ))}
      <Button
        size="small"
        onClick={() => setShow((s) => !s)}
        sx={{ alignSelf: "flex-start", px: 0 }}
        endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
      >
        View commit
      </Button>
      {show && (
        <Box
          component="pre"
          sx={{
            m: 0,
            p: 1,
            borderRadius: 0.5,
            bgcolor: "background.neutral",
            typography: "s3",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            whiteSpace: "pre-wrap",
          }}
        >
          {`commit ${b?.commit ?? ""} · ${b?.branch ?? "main"} · ${b?.repo ?? ""}\n${files.map((f) => `M ${f.path}  +${f.plus} −${f.minus}`).join("\n") || "No file changes recorded"}\n\n(Static diff: the prototype does not fetch the repository.)`}
        </Box>
      )}
    </Stack>
  );
}

CodeDiff.propTypes = { a: PropTypes.object, b: PropTypes.object };

const keysOf = (...objs) => [
  ...new Set(objs.flatMap((o) => Object.keys(o ?? {}))),
];

/**
 * The diff behind one ConfigDiffStrip chip (560 px popover body): prompt
 * words per message, model / params table, tools, agent nodes, code files,
 * harness key / values, or the two kinds side by side.
 */
const ConfigDiffDetail = ({ chip, a, b, labelA, labelB }) => {
  const ca = a?.config ?? {};
  const cb = b?.config ?? {};
  const extra = r4Rows(
    chip.axis,
    provenanceOf({ config: ca }),
    provenanceOf({ config: cb }),
    ca,
    cb,
  );
  if (extra)
    return (
      <TwoColumns
        labelA={labelA}
        labelB={labelB}
        rows={extra.filter(([, x, y]) => x != null || y != null)}
      />
    );
  switch (chip.axis) {
    case "prompt":
      return <PromptDiff a={ca.prompt} b={cb.prompt} />;
    case "model":
    case "params":
      return (
        <TwoColumns
          labelA={labelA}
          labelB={labelB}
          rows={[
            [
              "model",
              ca.model?.id ?? chip.detail?.from,
              cb.model?.id ?? chip.detail?.to,
            ],
            ...keysOf(ca.model?.params, cb.model?.params).map((k) => [
              k,
              ca.model?.params?.[k],
              cb.model?.params?.[k],
            ]),
            // R4 declared parameters (fi.serve / provider-exposed).
            ...keysOf(ca.params?.values, cb.params?.values).map((k) => [
              k,
              ca.params?.values?.[k],
              cb.params?.values?.[k],
            ]),
          ]}
        />
      );
    case "tools":
      return <ToolsDiff a={ca.tools} b={cb.tools} />;
    case "agent":
      return <AgentDiff a={ca.agent} b={cb.agent} />;
    case "code":
      return <CodeDiff a={ca.code} b={cb.code} />;
    case "harness":
      return (
        <TwoColumns
          labelA={labelA}
          labelB={labelB}
          rows={keysOf(ca.harness, cb.harness).map((k) => [
            k.replace(/_/g, " "),
            ca.harness?.[k],
            cb.harness?.[k],
          ])}
        />
      );
    case "kind":
      return (
        <TwoColumns
          labelA={labelA}
          labelB={labelB}
          rows={[
            ["kind", kindDiffLabel(ca), kindDiffLabel(cb)],
            [
              "prompt",
              ca.prompt ? `${ca.prompt.name} ${ca.prompt.version}` : null,
              cb.prompt ? `${cb.prompt.name} ${cb.prompt.version}` : null,
            ],
            [
              "model",
              ca.model?.id ?? ca.harness?.model,
              cb.model?.id ?? cb.harness?.model,
            ],
            [
              "agent",
              ca.agent ? `${ca.agent.name} ${ca.agent.version}` : null,
              cb.agent ? `${cb.agent.name} ${cb.agent.version}` : null,
            ],
            ["harness", ca.harness?.name, cb.harness?.name],
            [
              "framework",
              ca.framework?.name ?? null,
              cb.framework?.name ?? null,
            ],
            [
              "voice",
              ca.voice
                ? provenanceOf({ config: ca }).voice?.providerName
                : null,
              cb.voice
                ? provenanceOf({ config: cb }).voice?.providerName
                : null,
            ],
            [
              "code",
              ca.code ? `${ca.code.repo}@${ca.code.commit}` : null,
              cb.code ? `${cb.code.repo}@${cb.code.commit}` : null,
            ],
            [
              "tools",
              (ca.tools ?? []).map((t) => t.name).join(", "),
              (cb.tools ?? []).map((t) => t.name).join(", "),
            ],
          ].filter(([, x, y]) => x != null || y != null)}
        />
      );
    default:
      return (
        <TwoColumns
          labelA={labelA}
          labelB={labelB}
          rows={[[chip.axis, ca[chip.axis], cb[chip.axis]]]}
        />
      );
  }
};

ConfigDiffDetail.propTypes = {
  chip: PropTypes.object.isRequired,
  a: PropTypes.object,
  b: PropTypes.object,
  labelA: PropTypes.string,
  labelB: PropTypes.string,
};

export default ConfigDiffDetail;
