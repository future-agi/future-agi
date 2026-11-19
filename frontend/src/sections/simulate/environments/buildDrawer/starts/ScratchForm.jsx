import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from "@mui/material";
import { Field, HintLine } from "../forms/formBits";
import {
  BUILD_FILES_ACCEPT,
  mergeBuildFiles,
} from "../forms/shared/buildFiles";
import { T12 } from "../drawerStyles";
import { paths } from "src/routes/paths";
import { useDesignDraftStore } from "../../design/useDesignDraftStore";
import DescribeFlowCard from "./DescribeFlowCard";
import { PERSONAL_DATA } from "./startUtils";

const CHIPS = [
  "The agent gets triggered when…",
  "It reaches for these tools…",
  "It's done when…",
  "It escalates if…",
];

/**
 * Build from scratch: name it (or not) and describe the flow, or drop files
 * to learn from. Build environment opens the proposed design to approve;
 * nothing is built yet.
 */
export default function ScratchForm() {
  const navigate = useNavigate();
  const createDesignDraft = useDesignDraftStore((s) => s.createDesignDraft);
  const [name, setName] = useState("");
  const [flowText, setFlowText] = useState("");
  const [attachments, setAttachments] = useState([]);
  // Asked up front and starting on the safest choice, never a silent default.
  const [personalData, setPersonalData] = useState("replace");
  const canGo = flowText.trim().length >= 10 || attachments.length > 0;

  const addFiles = (list) =>
    setAttachments((prev) => mergeBuildFiles(prev, list));

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
        description="Trigger, tools, hand-offs. Or attach SOPs, CSVs or notes."
        placeholder="e.g. A customer emails a refund request. The agent looks up the order, checks the policy and drafts a reply. Over $500 it escalates to a human."
        chips={CHIPS}
        accept={BUILD_FILES_ACCEPT}
        value={flowText}
        onChange={setFlowText}
        files={attachments}
        onAdd={addFiles}
        onRemove={(id) =>
          setAttachments((prev) => prev.filter((a) => a.id !== id))
        }
        onNext={() =>
          navigate(
            paths.dashboard.simulate.environments.design(
              createDesignDraft({
                name,
                description: flowText,
                files: attachments,
                personalChoice: personalData,
              }),
            ),
          )
        }
        canSubmit={canGo}
        submitLabel="Build environment"
        filesFooter={
          <Stack
            direction="row"
            alignItems="center"
            flexWrap="wrap"
            columnGap={2}
            rowGap={0.5}
            sx={{ mt: 1, px: 0.25 }}
          >
            <Typography
              component="div"
              sx={{ ...T12, color: "text.secondary" }}
            >
              <HintLine
                text={`Personal data in ${attachments.length === 1 ? "this file" : "these files"}`}
                hint="Files are read in a cloud sandbox, not on your machine. Replace swaps names, emails and numbers for stand-ins. The same value always gets the same stand-in."
              />
            </Typography>
            <RadioGroup
              row
              aria-label="Personal data in the files"
              value={personalData}
              onChange={(e) => setPersonalData(e.target.value)}
              sx={{ columnGap: 2, flexWrap: "wrap" }}
            >
              {PERSONAL_DATA.map(([v, label]) => (
                <FormControlLabel
                  key={v}
                  value={v}
                  control={<Radio size="small" sx={{ p: 0.5 }} />}
                  sx={{ m: 0, gap: 0.25 }}
                  label={<Typography sx={T12}>{label}</Typography>}
                />
              ))}
            </RadioGroup>
          </Stack>
        }
      />
    </Stack>
  );
}
