import { Box, Typography, Link } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import CreateNewAgentCards from "../../CreateNewAgentCards";

import { useFormContext } from "react-hook-form";

const AgentBehaviourStep = ({ control }) => {
  const { data: knowledgeBaseList } = useKnowledgeBaseList("", null, {
    status: true,
  });

  // const [openKnowledgeBase, setOpenKnowledgeBase] = useState(false);
  useFormContext();
  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );
  // const queryClient = useQueryClient();

  return (
    <Box display={"flex"} flexDirection={"column"} gap={3}>
      <Box display={"flex"} flexDirection={"column"}>
        <Typography
          typography="m2"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Behavior Configuration
        </Typography>
        <Typography
          typography="s1"
          fontWeight="fontWeightRegular"
          color="text.disabled"
        >
          Define how your agent thinks, responds, and handles interactions.
        </Typography>
      </Box>
      <Box display="flex" flexDirection="column" gap={2}>
        <FormTextFieldV2
          control={control}
          fieldName="description"
          label="Prompt / Chains"
          required
          placeholder={
            "Write your agent’s system prompt, instructions, workflows, and conversation logic. You can define personality, tone, rules, and conversation flows.\nExample: ‘You are a friendly support agent…’"
          }
          size="small"
          multiline
          rows={7}
          fullWidth
          sx={{
            "& .MuiInputLabel-root": {
              fontWeight: 500,
            },
          }}
        />
        <Box mt={1}>
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="center"
          >
            <Typography typography="m2" fontWeight="fontWeightMedium">
              Knowledge Base
            </Typography>
            <Link
              href="https://docs.futureagi.com/docs/knowledge-base"
              color="blue.500"
              target="_blank"
              rel="noopener noreferrer"
              fontWeight="fontWeightMedium"
              fontSize="14px"
              sx={{ textDecoration: "underline" }}
            >
              Learn more
            </Link>
          </Box>

          <Typography variant="body2" color="text.primary">
            Provide domain-specific information to help agent behaviour as per
            your business use-case
          </Typography>
        </Box>
        <FormSearchSelectFieldControl
          disabled={false}
          label="Select Knowledge Base"
          placeholder="Select"
          size="small"
          control={control}
          fieldName={`knowledgeBase`}
          fullWidth
          sx={{
            "& .MuiInputLabel-root": {
              fontWeight: 500,
            },
          }}
          // createLabel="Create knowledge base"
          // handleCreateLabel={() => setOpenKnowledgeBase(true)}
          options={knowledgeBaseOptions}
          emptyMessage={"No knowledge base has been added"}
        />
        <CreateNewAgentCards
          title={"Commit message"}
          subtitle={
            "Add a short description of what is it in this version for commit history tracking"
          }
        >
          <Box display="flex" flexDirection="column">
            <FormTextFieldV2
              control={control}
              required
              fieldName="commitMessage"
              label="Commit Message"
              placeholder="My first version"
              size="small"
              fullWidth
              sx={{
                "& .MuiInputLabel-root": {
                  fontWeight: 500,
                },
              }}
            />
          </Box>
          {/* we are temporarily not allowing user to create  Knowledge Base from here due to some cases we will comeback with a solution soon  */}
          {/* <CreateKnowledgeBaseDrawer
            open={openKnowledgeBase}
            onClose={() => setOpenKnowledgeBase(false)}
            setHasData={null}
            refreshGrid={(id) => {
              queryClient.invalidateQueries(["knowledge-base"]);
              setValue("knowledgeBase", id);
            }}. 
          /> */}
        </CreateNewAgentCards>
      </Box>
    </Box>
  );
};

AgentBehaviourStep.propTypes = {
  control: PropTypes.object,
  errors: PropTypes.object,
  trigger: PropTypes.func,
};

export default AgentBehaviourStep;
