import { isActiveSkillJob } from 'src/api/skills/testing';

// Only shared, current, inspectable and finished evidence can be attached. The dialogs use this to
// say "attached" only while it holds; the server checks the attachment again on move or publish.
export const canAttachRun = (run) =>
  Boolean(
    run?.canAttach &&
      run.visibility === 'workspace' &&
      run.currentness?.content &&
      run.currentness?.payloadAvailable &&
      !isActiveSkillJob(run.status),
  );
