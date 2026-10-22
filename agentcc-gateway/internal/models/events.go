package models

// EventSink takes one gateway event for the request-log webhook. The flusher's
// EnqueueEvent is the sink in a running gateway: it adds {"kind": kind,
// ...payload} to the next batch, next to the log entries. Delivery is best
// effort, so money never depends on an event.
type EventSink func(kind string, payload any)

// EventKindOutcome is the kind of an OutcomeEvent.
const EventKindOutcome = "outcome"

// DiscardEvents is the EventSink that drops every event: the default when no
// log webhook is configured.
func DiscardEvents(string, any) {}

// OutcomeEvent is the payload of an outcome event (outcome-event-v1). It
// carries no content. The sink adds the kind.
type OutcomeEvent struct {
	EventID      string   `json:"event_id"`
	OrgID        string   `json:"org_id"`
	GenerationID string   `json:"generation_id"`
	Outcome      string   `json:"outcome"` // ok, bad, empty, verify_failed or retried
	Source       string   `json:"source"`  // feedback or prev_outcome
	Score        *float64 `json:"score,omitempty"`
	AgentRunID   string   `json:"agent_run_id,omitempty"`
	AgentID      string   `json:"agent_id,omitempty"`
	AgentStep    Step     `json:"agent_step,omitempty"`
	Model        string   `json:"model,omitempty"`
	TsMs         int64    `json:"ts_ms"`
}
