package models

// Step is what an agent call is for. Auto routing is step-aware.
type Step string

const (
	StepPlan       Step = "plan"
	StepAct        Step = "act"
	StepTool       Step = "tool"
	StepExtract    Step = "extract"
	StepVerify     Step = "verify"
	StepReflect    Step = "reflect"
	StepMemory     Step = "memory"
	StepCoordinate Step = "coordinate"
	StepSummarize  Step = "summarize"
	StepFinal      Step = "final"
	StepOther      Step = "other"
)

// Steps lists every Step, in the enum's order.
var Steps = []Step{StepPlan, StepAct, StepTool, StepExtract, StepVerify, StepReflect, StepMemory, StepCoordinate, StepSummarize, StepFinal, StepOther}

// Valid reports whether s is one of Steps.
func (s Step) Valid() bool {
	for _, v := range Steps {
		if s == v {
			return true
		}
	}
	return false
}

// Tier is how hard a task is.
type Tier string

const (
	TierSimple   Tier = "simple"
	TierStandard Tier = "standard"
	TierComplex  Tier = "complex"
)

// Where an agent's identity came from (log-entry-v2 agent.identity_source).
// An alias source is "alias:<framework>", see IdentitySourceAlias.
const (
	IdentitySourceRuntimeToken = "runtime_token"
	IdentitySourceHeader       = "header"
	IdentitySourceBody         = "body"
)

// IdentitySourceAlias is the identity source for a framework alias header.
func IdentitySourceAlias(framework string) string { return "alias:" + framework }

// AgentContext is the typed agent identity of one call, with its attribution
// fields (app, tags, end user). The gateway's parse and apply hooks fill
// it from runtime-token claims, headers, framework aliases and the body; the
// gateway reads it for routing, budgets, logs and analytics. Empty means the
// caller sent no agent context.
type AgentContext struct {
	Framework    string
	AgentID      string
	AgentVersion string
	RunID        string
	ParentRunID  string
	// RootRunID comes only from a runtime token's root_run_id claim.
	RootRunID string
	TaskID    string
	TraceID   string
	Workflow  string

	Step       Step
	StepSource string // "header", "body" or "inferred"
	Turn       *int   // nil when the caller sent none; 0 is a real turn

	// BudgetScope is the run-budget scope (header budget-scope, alias
	// ledger-id), preferred over RunID.
	BudgetScope        string
	BudgetUSD          *float64
	BudgetRemainingUSD *float64
	MaxCostUSD         *float64
	AttemptID          string
	DeadlineMs         int // 0 when none
	RouteHint          string
	Require            []string
	PrevOutcome        string
	Env                string
	UserID             string
	WorkspaceID        string

	// SessionID is the resolved session (header, then body precedence). The log
	// carries it at top level as session_id, never inside agent{}.
	SessionID string

	IdentitySource  string   // see the IdentitySource constants
	AliasOverridden []string // alias headers a canonical header overrode

	// Attribution (app, tags and end_user).
	AppTitle   string
	AppReferer string
	EndUser    string
	Tags       []string
}

// Clone returns a deep copy of a.
func (a AgentContext) Clone() AgentContext {
	a.Turn = cloneInt(a.Turn)
	a.BudgetUSD = cloneFloat(a.BudgetUSD)
	a.BudgetRemainingUSD = cloneFloat(a.BudgetRemainingUSD)
	a.MaxCostUSD = cloneFloat(a.MaxCostUSD)
	a.Require = cloneStrings(a.Require)
	a.AliasOverridden = cloneStrings(a.AliasOverridden)
	a.Tags = cloneStrings(a.Tags)
	return a
}

func cloneStrings(s []string) []string {
	if s == nil {
		return nil
	}
	return append([]string(nil), s...)
}

func cloneInt(p *int) *int {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}

func cloneInt64(p *int64) *int64 {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}

func cloneFloat(p *float64) *float64 {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}

func cloneBool(p *bool) *bool {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}
