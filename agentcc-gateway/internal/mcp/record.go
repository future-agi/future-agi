package mcp

import (
	"encoding/json"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// EventKindToolCall is the event kind a tool-call record is emitted under
// (Deps.Events), next to the request log's own kinds.
const EventKindToolCall = "tool_call"

// ToolCallRecord is the shape of one mcp-tool-call-v1 record, for one
// logical tools/call: allowed, denied, failed, timed out or cancelled. It
// carries no secret, token or session id; Arguments and Result are set only
// under content mode full, and never past cc.Terms.Retention.
type ToolCallRecord struct {
	V               int       `json:"v"`
	EventID         string    `json:"event_id"` // = ToolCallID
	OrgID           string    `json:"org_id"`
	RequestID       string    `json:"request_id"`
	ToolCallID      string    `json:"tool_call_id"`
	TsMs            int64     `json:"ts_ms"`
	StartedAt       time.Time `json:"started_at"`
	APIKeyID        string    `json:"api_key_id"`
	PrincipalKind   string    `json:"principal_kind"`
	UserID          string    `json:"user_id,omitempty"`
	ClientID        string    `json:"client_id,omitempty"`
	VirtualServerID string    `json:"virtual_server_id"`
	ServerID        string    `json:"server_id"`
	Tool            string    `json:"tool"`
	UpstreamTool    string    `json:"upstream_tool"`
	ToolRevision    string    `json:"tool_revision,omitempty"`
	Via             string    `json:"via,omitempty"` // "call_tool" through the meta-tool
	Era             string    `json:"era"`
	ProtocolVersion string    `json:"protocol_version"`
	SessionHash     string    `json:"session_hash,omitempty"`

	Agent        ToolCallAgent `json:"agent"`
	GenerationID string        `json:"generation_id,omitempty"`

	Outcome      string             `json:"outcome"`
	IsError      bool               `json:"is_error"`
	ErrorClass   models.ErrorClass  `json:"error_class"`
	ErrorOrigin  models.ErrorOrigin `json:"error_origin"`
	ErrorCode    string             `json:"error_code,omitempty"`
	ErrorMessage string             `json:"error_message,omitempty"` // at most 512 bytes, scrubbed

	LatencyMs         int64 `json:"latency_ms"`
	UpstreamLatencyMs int64 `json:"upstream_latency_ms"`
	GatewayOverheadMs int64 `json:"gateway_overhead_ms"`
	BytesIn           int64 `json:"bytes_in"`
	BytesOut          int64 `json:"bytes_out"`

	BillingMode        string `json:"billing_mode"`
	CredentialType     string `json:"credential_type"`
	CredentialSource   string `json:"credential_source"`
	FallbackFrom       string `json:"fallback_from,omitempty"`
	UpstreamCostMicros int64  `json:"upstream_cost_micros"`
	BilledCostMicros   int64  `json:"billed_cost_micros"`
	MarkupMicros       int64  `json:"markup_micros"`
	MarkupBps          int    `json:"markup_bps"`
	CostSource         string `json:"cost_source"`
	UsageEstimated     int    `json:"usage_estimated"` // 0 or 1
	Dispatched         bool   `json:"dispatched"`
	BudgetScope        string `json:"budget_scope,omitempty"`

	Guardrail  ToolCallGuardrail `json:"guardrail"`
	ApprovalID string            `json:"approval_id,omitempty"`

	ContentMode string          `json:"content_mode"`
	Arguments   json.RawMessage `json:"arguments,omitempty"`
	Result      json.RawMessage `json:"result,omitempty"`

	// Optional fields from cc.Evidence.
	Identity     json.RawMessage `json:"identity,omitempty"`
	AccountLabel json.RawMessage `json:"account_label,omitempty"`
	Policy       json.RawMessage `json:"policy,omitempty"`
	Flow         json.RawMessage `json:"flow,omitempty"`
	Shaped       json.RawMessage `json:"shaped,omitempty"`
}

// ToolCallAgent is the record's agent block.
type ToolCallAgent struct {
	Framework      string `json:"framework"`
	AgentID        string `json:"agent_id"`
	AgentVersion   string `json:"agent_version"`
	RunID          string `json:"run_id"`
	ParentRunID    string `json:"parent_run_id"`
	RootRunID      string `json:"root_run_id"`
	TaskID         string `json:"task_id"`
	TraceID        string `json:"trace_id"`
	Workflow       string `json:"workflow"`
	Step           string `json:"step"`
	Turn           *int   `json:"turn"`
	SessionID      string `json:"session_id"`
	IdentitySource string `json:"identity_source"`
}

// ToolCallGuardrail lists the guardrail checks a call went through.
type ToolCallGuardrail struct {
	Checked []string `json:"checked"`
	Blocked []string `json:"blocked"`
	Masked  []string `json:"masked"`
	Shadow  []string `json:"shadow"`
}

// Tool-call outcomes.
const (
	OutcomeSuccess   = "success"
	OutcomeIsError   = "is_error"
	OutcomeDenied    = "denied"
	OutcomeError     = "error"
	OutcomeTimeout   = "timeout"
	OutcomeCancelled = "cancelled"
)

// The MCP error classes, and the network origin.
const (
	ErrClassDenied           models.ErrorClass  = "denied"
	ErrClassQuarantined      models.ErrorClass  = "quarantined"
	ErrClassApprovalRequired models.ErrorClass  = "approval_required"
	ErrClassAuthRequired     models.ErrorClass  = "auth_required"
	ErrClassInvalidArguments models.ErrorClass  = "invalid_arguments"
	ErrClassProtocol         models.ErrorClass  = "protocol"
	OriginNetwork            models.ErrorOrigin = "network"
)
