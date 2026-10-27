import { useState } from "react";
import { Stack, Typography } from "@mui/material";
import { Field, HintLine } from "../forms/formBits";
import { T12 } from "../drawerStyles";
import DescribeFlowCard from "./DescribeFlowCard";
import { uiOnlyToast } from "./startUtils";

const CHIPS = [
  "The agent gets triggered when…",
  "It reaches for these tools…",
  "It's done when…",
  "It escalates if…",
];

/**
 * Build from scratch: name it (or not) and describe the flow, or drop files
 * to learn from. UI only: building from a description isn't wired yet.
 */
export default function ScratchForm() {
  const [name, setName] = useState("");
  const [flowText, setFlowText] = useState("");
  const [attachments, setAttachments] = useState([]);
  const canGo = flowText.trim().length >= 10 || attachments.length > 0;

  const addFiles = (list) =>
    setAttachments((prev) => {
      const seen = new Set(prev.map((a) => a.id));
      return [...prev, ...list.filter((a) => !seen.has(a.id))];
    });

  return (
    <Stack spacing={2} sx={{ p: 2.5 }}>
      <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
        <HintLine
          text="No agent yet? Fine. Connect it later."
          hint="We set up the world, personas and evals from your description. Connect the agent from Overview when it's ready. Runs start once it is."
        />
      </Typography>
      <Field
        label={
          <HintLine
            text="Environment name (optional)"
            hint="Leave it blank and we'll name it from your description."
          />
        }
        inputProps={{ "aria-label": "Environment name" }}
        placeholder="e.g. Support desk sandbox"
        value={name}
        onChange={setName}
      />
      <DescribeFlowCard
        prompt="Describe the flow, or drop files to learn from"
        description="Trigger, tools, hand-offs. Or attach SOPs, CSVs or calls."
        placeholder="e.g. A customer emails a refund request. The agent looks up the order, checks the policy and drafts a reply. Over $500 it escalates to a human."
        chips={CHIPS}
        accept=".csv,.tsv,.json,.md,.txt,.pdf,.docx,.wav,.mp3,.m4a"
        value={flowText}
        onChange={setFlowText}
        files={attachments}
        onAdd={addFiles}
        onRemove={(id) =>
          setAttachments((prev) => prev.filter((a) => a.id !== id))
        }
        onNext={() => uiOnlyToast("building from a description")}
        canSubmit={canGo}
        submitLabel="Build environment"
      />
    </Stack>
  );
}
