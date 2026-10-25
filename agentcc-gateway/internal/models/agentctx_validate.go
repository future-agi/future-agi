package models

import (
	"fmt"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// The agent context fields, by index. A field is sent as
// the header x-agentcc-<suffix> (or its exact alias x-omega-<suffix>), or in
// the body's metadata.agent or metadata.omega object under its snake_case
// key. AgentFieldSuffixes holds each field's header suffix.
const (
	AgentFieldAgentID = iota
	AgentFieldAgentVersion
	AgentFieldRunID
	AgentFieldParentRunID
	AgentFieldTaskID
	AgentFieldTraceID
	AgentFieldWorkflow
	AgentFieldStep
	AgentFieldTurn
	AgentFieldBudgetScope
	AgentFieldBudgetUSD
	AgentFieldBudgetRemainingUSD
	AgentFieldMaxCostUSD
	AgentFieldAttemptID
	AgentFieldDeadlineMs
	AgentFieldRouteHint
	AgentFieldRequire
	AgentFieldPrevOutcome
	AgentFieldEnv
	AgentFieldFramework
	AgentFieldUserID
	AgentFieldWorkspaceID
	AgentFieldCount
)

// AgentFieldSuffixes is each field's header suffix, indexed by field.
var AgentFieldSuffixes = [AgentFieldCount]string{
	"agent-id", "agent-version", "run-id", "parent-run-id", "task-id", "trace-id",
	"workflow", "step", "turn", "budget-scope", "budget-usd", "budget-remaining-usd",
	"max-cost-usd", "attempt-id", "deadline-ms", "route-hint", "require",
	"prev-outcome", "env", "framework", "user-id", "workspace-id",
}

// agentFieldAliases are second names for a field: ledger-id is budget-scope.
var agentFieldAliases = map[string]int{"ledger-id": AgentFieldBudgetScope}

var (
	agentFieldBySuffix = buildAgentFieldIndex(func(s string) string { return s })
	agentFieldByKey    = buildAgentFieldIndex(func(s string) string { return strings.ReplaceAll(s, "-", "_") })
)

func buildAgentFieldIndex(name func(string) string) map[string]int {
	m := make(map[string]int, AgentFieldCount+len(agentFieldAliases))
	for i, s := range AgentFieldSuffixes {
		m[name(s)] = i
	}
	for s, i := range agentFieldAliases {
		m[name(s)] = i
	}
	return m
}

// AgentFieldBySuffix returns the field a lower-case header suffix names
// ("run-id" for x-agentcc-run-id).
func AgentFieldBySuffix(suffix string) (int, bool) {
	i, ok := agentFieldBySuffix[suffix]
	return i, ok
}

// AgentFieldByKey returns the field a body key names ("run_id" in
// metadata.agent).
func AgentFieldByKey(key string) (int, bool) {
	i, ok := agentFieldByKey[key]
	return i, ok
}

// Value limits of the agent context fields.
const (
	maxAgentIDLen     = 128
	MaxAgentUserIDLen = 255 // bytes: request_log.user_id is varchar(255), as session_id
	maxAgentLabelLen  = 32  // env and framework: char32 log columns
	maxAgentTurn      = 100000
	maxAgentUSD       = 1e6
	maxAgentDeadline  = 3600000
	MaxAgentTags      = 10
	maxAgentTagLen    = 64
	maxAgentUSDDigits = 32
)

// AgentRequireCaps, AgentRouteHints and AgentPrevOutcomes are the values the
// require, route-hint and prev-outcome fields accept.
var (
	AgentRequireCaps  = []string{"tools", "json_schema", "vision", "reasoning", "audio", "long_context"}
	AgentRouteHints   = []string{"cost", "balanced", "quality", "latency"}
	AgentPrevOutcomes = []string{"ok", "empty", "verify_failed", "retried"}
)

// AgentFieldError is a value its field does not accept.
type AgentFieldError struct {
	Field  int
	Reason string
}

func (e *AgentFieldError) Error() string {
	return AgentFieldSuffixes[e.Field] + ": " + e.Reason
}

// SetAgentField validates value against field's rule and stores it on a.
// value has had its surrounding spaces trimmed by the caller.
func (a *AgentContext) SetAgentField(field int, value string) *AgentFieldError {
	fail := func(reason string) *AgentFieldError { return &AgentFieldError{Field: field, Reason: reason} }
	switch field {
	case AgentFieldStep:
		s := Step(value)
		if !s.Valid() {
			return fail("must be one of " + joinSteps())
		}
		a.Step = s
	case AgentFieldTurn:
		n, err := strconv.Atoi(value)
		if err != nil || n < 0 || n > maxAgentTurn {
			return fail(fmt.Sprintf("must be an integer from 0 to %d", maxAgentTurn))
		}
		a.Turn = &n
	case AgentFieldBudgetUSD, AgentFieldBudgetRemainingUSD, AgentFieldMaxCostUSD:
		v, ok := parseAgentUSD(value)
		if !ok {
			return fail("must be a decimal number of US dollars from 0 to 1000000")
		}
		switch field {
		case AgentFieldBudgetUSD:
			a.BudgetUSD = &v
		case AgentFieldBudgetRemainingUSD:
			a.BudgetRemainingUSD = &v
		default:
			a.MaxCostUSD = &v
		}
	case AgentFieldDeadlineMs:
		n, err := strconv.Atoi(value)
		if err != nil || n < 1 || n > maxAgentDeadline {
			return fail(fmt.Sprintf("must be an integer number of milliseconds from 1 to %d", maxAgentDeadline))
		}
		a.DeadlineMs = n
	case AgentFieldRouteHint:
		if !oneOf(value, AgentRouteHints) {
			return fail("must be one of " + strings.Join(AgentRouteHints, ", "))
		}
		a.RouteHint = value
	case AgentFieldPrevOutcome:
		if !oneOf(value, AgentPrevOutcomes) {
			return fail("must be one of " + strings.Join(AgentPrevOutcomes, ", "))
		}
		a.PrevOutcome = value
	case AgentFieldRequire:
		caps, ok := parseAgentRequire(value)
		if !ok {
			return fail("must be a comma-separated list of " + strings.Join(AgentRequireCaps, ", "))
		}
		a.Require = caps
	case AgentFieldUserID:
		if !ValidAgentUserID(value) {
			return fail(AgentUserIDRule)
		}
		a.UserID = value
	case AgentFieldEnv, AgentFieldFramework:
		if !validAgentChars(value, maxAgentLabelLen) {
			return fail(fmt.Sprintf("must be 1 to %d characters of A-Z a-z 0-9 . _ : @ / -", maxAgentLabelLen))
		}
		if field == AgentFieldEnv {
			a.Env = value
		} else {
			a.Framework = value
		}
	default:
		if !ValidAgentID(value) {
			return fail(fmt.Sprintf("must be 1 to %d characters of A-Z a-z 0-9 . _ : @ / -", maxAgentIDLen))
		}
		*a.idField(field) = value
	}
	return nil
}

// idField is the string field an id-valued agent field is stored in.
func (a *AgentContext) idField(field int) *string {
	switch field {
	case AgentFieldAgentID:
		return &a.AgentID
	case AgentFieldAgentVersion:
		return &a.AgentVersion
	case AgentFieldRunID:
		return &a.RunID
	case AgentFieldParentRunID:
		return &a.ParentRunID
	case AgentFieldTaskID:
		return &a.TaskID
	case AgentFieldTraceID:
		return &a.TraceID
	case AgentFieldWorkflow:
		return &a.Workflow
	case AgentFieldBudgetScope:
		return &a.BudgetScope
	case AgentFieldAttemptID:
		return &a.AttemptID
	case AgentFieldUserID:
		return &a.UserID
	case AgentFieldWorkspaceID:
		return &a.WorkspaceID
	}
	panic(fmt.Sprintf("models: agent field %d is not an id", field))
}

// RuntimeConflictError is a header or body value that disagrees with a
// runtime-token claim (agent_header_conflict, metadata.source=runtime_token).
// Field is the agent field, or "org_id" or "deployment_id"; Reason is
// "run_outside_root" for a run the token does not cover.
type RuntimeConflictError struct {
	Field  string
	Reason string
}

func (e *RuntimeConflictError) Error() string {
	if e.Reason != "" {
		return e.Field + ": " + e.Reason
	}
	return e.Field + " disagrees with the runtime token"
}

// RuntimeReasonRunOutsideRoot is a run id outside the token's root run scope.
const RuntimeReasonRunOutsideRoot = "run_outside_root"

// ApplyRuntimeClaims merges a verified runtime token's claims into a, which
// holds what the headers and body sent (runtime-token-claims-v1). The org,
// deployment and agent are the claims'; a sent value
// that differs is a conflict. The workspace, agent version and env claims win
// over sent values. The root run scope is the claims': a call with no run id
// runs as the root, and a sent run id other than the root names a descendant,
// accepted when its sent parent is the root, when the gateway verified it in
// the root's lineage (rt.DescendantVerified), or when its sent budget scope is
// the claim's. Every call keeps the claim's budget scope (default: the root),
// and a sent budget can only lower the claim's. orgID and deploymentID are
// the sent values ("" when none).
func (a *AgentContext) ApplyRuntimeClaims(rt *RuntimeIdentity, orgID, deploymentID string) *RuntimeConflictError {
	for _, c := range [...]struct{ field, sent, claim string }{
		{"org_id", orgID, rt.OrgID}, {"deployment_id", deploymentID, rt.DeploymentID}, {"agent_id", a.AgentID, rt.AgentID},
	} {
		if c.sent != "" && c.claim != "" && c.sent != c.claim {
			return &RuntimeConflictError{Field: c.field}
		}
	}
	claimed := func(dst *string, claim string) {
		if claim != "" {
			*dst = claim
		}
	}
	claimed(&a.AgentID, rt.AgentID)
	claimed(&a.WorkspaceID, rt.WorkspaceID)
	claimed(&a.AgentVersion, rt.AgentVersion)
	claimed(&a.Env, rt.Env)

	scope := rt.BudgetScope
	if scope == "" {
		scope = rt.RootRunID
	}
	if root := rt.RootRunID; root != "" {
		switch run := a.RunID; {
		case run == "" || run == root:
			a.RunID = root
			claimed(&a.ParentRunID, rt.ParentRunID)
		case a.ParentRunID == root || rt.DescendantVerified || (a.BudgetScope != "" && a.BudgetScope == scope):
		default:
			return &RuntimeConflictError{Field: "run_id", Reason: RuntimeReasonRunOutsideRoot}
		}
		a.RootRunID = root
	}
	claimed(&a.BudgetScope, scope)
	if rt.BudgetUSD != nil && (a.BudgetUSD == nil || *a.BudgetUSD > *rt.BudgetUSD) {
		a.BudgetUSD = cloneFloat(rt.BudgetUSD)
	}
	return nil
}

// AgentUserIDRule is the user-id rule as the 400 agent_header_invalid states
// it.
const AgentUserIDRule = "must be 1 to 255 bytes of printable characters: letters, marks, numbers, " +
	"punctuation, symbols and the space; no control, invisible or invalid UTF-8 characters"

// ValidAgentUserID reports whether s is a valid user-id: 1 to 255 bytes of
// valid UTF-8 whose every character prints (unicode.IsPrint: a letter, mark,
// number, punctuation, symbol or the ASCII space). The public docs present
// x-agentcc-user-id as a plain string ("jane+tag@example.com", "user 42"), so
// it is wider than the id rule the other id fields keep (they key Redis state:
// run budgets, affinity). Refused: control characters (tab, newline, NUL,
// DEL, C1), invisible ones (zero-width, byte-order mark, line separators,
// spaces other than U+0020) and invalid UTF-8. The limit is the request log's
// user_id column (varchar(255)) and the session id's (maxSessionIDLen).
func ValidAgentUserID(s string) bool {
	if s == "" || len(s) > MaxAgentUserIDLen {
		return false
	}
	for i := 0; i < len(s); {
		c := s[i]
		if c < utf8.RuneSelf {
			if c < ' ' || c == 0x7f {
				return false
			}
			i++
			continue
		}
		r, size := utf8.DecodeRuneInString(s[i:])
		if (r == utf8.RuneError && size == 1) || !unicode.IsPrint(r) {
			return false
		}
		i += size
	}
	return true
}

// ValidAgentID reports whether s matches ^[A-Za-z0-9._:@/-]{1,128}$.
func ValidAgentID(s string) bool { return validAgentChars(s, maxAgentIDLen) }

func validAgentChars(s string, maxLen int) bool {
	if s == "" || len(s) > maxLen {
		return false
	}
	for i := 0; i < len(s); i++ {
		if !agentIDByte(s[i]) {
			return false
		}
	}
	return true
}

func agentIDByte(c byte) bool {
	switch {
	case 'a' <= c && c <= 'z', 'A' <= c && c <= 'Z', '0' <= c && c <= '9':
		return true
	}
	return c == '.' || c == '_' || c == ':' || c == '@' || c == '/' || c == '-'
}

// parseAgentUSD accepts a plain decimal (digits with at most one point, no
// sign or exponent) from 0 to 1e6.
func parseAgentUSD(s string) (float64, bool) {
	if s == "" || len(s) > maxAgentUSDDigits {
		return 0, false
	}
	digits, points := 0, 0
	for i := 0; i < len(s); i++ {
		switch c := s[i]; {
		case '0' <= c && c <= '9':
			digits++
		case c == '.':
			points++
		default:
			return 0, false
		}
	}
	if digits == 0 || points > 1 {
		return 0, false
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil || v > maxAgentUSD {
		return 0, false
	}
	return v, true
}

func parseAgentRequire(s string) ([]string, bool) {
	var caps []string
	for _, part := range strings.Split(s, ",") {
		c := strings.TrimSpace(part)
		if !oneOf(c, AgentRequireCaps) {
			return nil, false
		}
		if !oneOf(c, caps) {
			caps = append(caps, c)
		}
	}
	return caps, len(caps) > 0
}

// ParseAgentTags parses an x-agentcc-tags comma list: trimmed, deduplicated
// in order, and valid under ValidAgentTags.
func ParseAgentTags(csv string) ([]string, error) {
	var tags []string
	for _, part := range strings.Split(csv, ",") {
		tag := strings.TrimSpace(part)
		if !oneOf(tag, tags) {
			tags = append(tags, tag)
		}
	}
	if err := ValidAgentTags(tags); err != nil {
		return nil, err
	}
	return tags, nil
}

// ValidAgentTags checks a request's tags: at most 10, each 1 to 64
// characters of A-Z a-z 0-9 . _ : / -.
func ValidAgentTags(tags []string) error {
	if len(tags) > MaxAgentTags {
		return fmt.Errorf("at most %d tags are allowed, got %d", MaxAgentTags, len(tags))
	}
	for _, tag := range tags {
		if !validAgentChars(tag, maxAgentTagLen) || strings.IndexByte(tag, '@') >= 0 {
			return fmt.Errorf("tag %q must be 1 to %d characters of A-Z a-z 0-9 . _ : / -", tag, maxAgentTagLen)
		}
	}
	return nil
}

// TruncateRunes returns s cut to at most n characters, never mid-rune.
func TruncateRunes(s string, n int) string {
	if len(s) <= n {
		return s
	}
	count := 0
	for i := range s {
		if count == n {
			return s[:i]
		}
		count++
	}
	return s
}

// RuneCount is the number of characters in s.
func RuneCount(s string) int { return utf8.RuneCountInString(s) }

func oneOf(v string, set []string) bool {
	for _, s := range set {
		if v == s {
			return true
		}
	}
	return false
}

func joinSteps() string {
	names := make([]string, len(Steps))
	for i, s := range Steps {
		names[i] = string(s)
	}
	return strings.Join(names, ", ")
}
