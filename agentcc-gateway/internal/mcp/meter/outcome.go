package meter

import (
	"strings"
)

// Record outcomes (mcp-tool-call-v1, MC.2).
const (
	OutcomeSuccess   = "success"
	OutcomeIsError   = "is_error"
	OutcomeDenied    = "denied"
	OutcomeError     = "error"
	OutcomeTimeout   = "timeout"
	OutcomeCancelled = "cancelled"
)

// Error origins (MC.2).
const (
	OriginGateway  = "gateway"
	OriginUpstream = "upstream"
	OriginClient   = "client"
	OriginNetwork  = "network"
)

// The record's error code for a call to an unknown or hidden tool (hook 6).
const CodeUnknownTool = "unknown_tool"

// codeUpstreamError is the denial code of a failed upstream call (MC.3), and
// its metadata.attribution values.
const (
	codeUpstreamError  = "upstream_error"
	attributionTimeout = "timeout"
	attributionNetwork = "network"
)

// Input is what classification reads from a finished call.
type Input struct {
	// Denied is set when a hook refused the call; Code and Attribution are
	// then the denial's code and its metadata.attribution, if any.
	Denied      bool
	Code        string
	Attribution string
	// HasResult and IsError describe the upstream result, when there is one.
	HasResult bool
	IsError   bool
	// Cancelled is set when the client went away before the call finished.
	Cancelled bool
	// TimedOut is set when the upstream call used its whole time budget.
	TimedOut bool
}

// Class is a finished call's outcome and, unless it succeeded, its error
// class, origin and code.
type Class struct {
	Outcome     string
	ErrorClass  string
	ErrorOrigin string
	ErrorCode   string
}

// Classify returns the record outcome of a finished call. An isError result
// is is_error, never success; an upstream failure is cancelled when the
// client went away, timeout when its time ran out, and error otherwise; any
// other denial is denied, classed by its code.
func Classify(in Input) Class {
	switch {
	case !in.Denied && in.HasResult && !in.IsError:
		return Class{Outcome: OutcomeSuccess}
	case !in.Denied && in.HasResult:
		return Class{Outcome: OutcomeIsError, ErrorClass: "unknown", ErrorOrigin: OriginUpstream}
	case !in.Denied && in.Cancelled:
		return Class{Outcome: OutcomeCancelled, ErrorClass: "cancelled", ErrorOrigin: OriginClient}
	case !in.Denied:
		// The chain ended with neither a result nor a denial.
		return Class{Outcome: OutcomeError, ErrorClass: "unknown", ErrorOrigin: OriginGateway}
	case in.Code == codeUpstreamError:
		return classifyUpstream(in)
	case in.Code == CodeUnknownTool || in.Code == "tool_not_found":
		return Class{Outcome: OutcomeDenied, ErrorClass: "denied", ErrorOrigin: OriginGateway, ErrorCode: CodeUnknownTool}
	default:
		return Class{Outcome: OutcomeDenied, ErrorClass: denialClass(in.Code), ErrorOrigin: OriginGateway, ErrorCode: in.Code}
	}
}

func classifyUpstream(in Input) Class {
	c := Class{Outcome: OutcomeError, ErrorCode: codeUpstreamError}
	switch {
	case in.Cancelled:
		c.Outcome, c.ErrorClass, c.ErrorOrigin = OutcomeCancelled, "cancelled", OriginClient
	case in.Attribution == attributionTimeout || in.TimedOut:
		c.Outcome, c.ErrorClass, c.ErrorOrigin = OutcomeTimeout, "timeout", OriginUpstream
	case in.Attribution == attributionNetwork:
		c.ErrorClass, c.ErrorOrigin = "network", OriginNetwork
	default:
		c.ErrorClass, c.ErrorOrigin = "unknown", OriginUpstream
	}
	return c
}

// denialClasses maps an MC.3 refusal code to the record's error class
// (plan section 6.1's classes plus MC.2's). A code not listed is denied.
var denialClasses = map[string]string{
	"tool_quarantined":                "quarantined",
	"approval_required":               "approval_required",
	"approval_mismatch":               "approval_required",
	"auth_required":                   "auth_required",
	"invalid_arguments":               "invalid_arguments",
	"input_required_unsupported":      "protocol",
	"request_state_invalid":           "protocol",
	"request_state_instance_mismatch": "protocol",
	"managed_credits_exhausted":       "insufficient_credits",
	"managed_credits_insufficient":    "insufficient_credits",
	"tool_budget_exhausted":           "budget",
	"agent_run_stopped":               "budget",
	"key_limit_exceeded":              "budget",
	"managed_tool_over_cap":           "budget",
	"managed_spend_paused":            "budget",
	"tool_rate_limited":               "rate_limited",
	"managed_spend_velocity":          "rate_limited",
	"tool_call_blocked":               "policy",
	"tool_result_blocked":             "policy",
}

func denialClass(code string) string {
	if c, ok := denialClasses[code]; ok {
		return c
	}
	return "denied"
}

// MaxErrorMessage is the most characters a record's error_message keeps, as
// for an attempt's (plan section 6.1).
const MaxErrorMessage = 512

// ErrorMessage is msg as a record keeps it: trimmed, scrubbed by scrub
// (credentials, then the org's redactor), then cut to MaxErrorMessage
// characters with NUL replaced (Column).
func ErrorMessage(msg string, scrub func(string) string) string {
	msg = strings.TrimSpace(msg)
	if msg == "" {
		return ""
	}
	if scrub != nil {
		msg = scrub(msg)
	}
	return Column(msg, MaxErrorMessage)
}
