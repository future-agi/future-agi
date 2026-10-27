import EmptyState from "../../components/EmptyState";

// The World tab: what the agent talks to during a run. Its tools, data and
// capability graph move here from Contract in the World task; until then the
// tab says what it will hold.
export default function WorldPanel() {
  return (
    <EmptyState
      icon="solar:globe-linear"
      title="The world"
      body="The data and the tool handlers your agent talks to during a run."
    />
  );
}
