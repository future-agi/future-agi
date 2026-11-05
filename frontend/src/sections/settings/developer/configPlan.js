/*
  Workspace settings as code (T38; clients.md "Later manifest management"):
  plan compares a settings file with a named workspace profile, apply makes
  the reviewed changes, promote copies approved settings from one workspace
  to another. Profiles name workspaces — never simulation environments — and
  nothing here deploys or changes the customer's agent.
*/

export const CONFIG_CMDS = `# What would change in the staging workspace — nothing is applied
fi config plan --profile staging -f futureagi.yaml

# Apply that reviewed plan
fi config apply --profile staging --plan plan-7f3a

# Copy the approved settings from staging to production
fi config promote --from staging --to production

# Undo a change, where it can be undone
fi config rollback --profile production --change ch-1042`;

export const PROFILES = {
  staging: { workspace: "Acme · Staging" },
  production: { workspace: "Acme · Production" },
};

/** One plan: each change, and how far it can be rolled back. */
export const SAMPLE_PLAN = {
  id: "plan-7f3a",
  profile: "staging",
  file: "futureagi.yaml @ 3c9e1d2",
  changes: [
    {
      op: "add",
      path: "evals/refund-policy",
      what: "New eval: refund policy followed",
      rollback: "Can be removed again",
    },
    {
      op: "change",
      path: "alerts/error-spike",
      what: "Threshold 5% → 3%",
      rollback: "Can be set back; alerts already sent stay sent",
    },
    {
      op: "change",
      path: "prompts/support-greeting",
      what: "Version v4 → v5 as the default",
      rollback: "Can switch back to v4",
    },
    {
      op: "remove",
      path: "datasets/old-returns-2025",
      what: "Dataset deleted",
      rollback: "Can't be undone — the rows are deleted",
      danger: true,
    },
  ],
};

export const OP_LABEL = { add: "Add", change: "Change", remove: "Remove" };
