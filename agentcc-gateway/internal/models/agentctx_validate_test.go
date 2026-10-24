package models

import (
	"strings"
	"testing"
)

func TestAgentHeadersFieldTable(t *testing.T) {
	// Every agent-context header suffix names its field, and the body key is
	// the same name in snake_case. ledger-id is budget-scope's alias.
	cases := map[string]int{
		"agent-id": AgentFieldAgentID, "agent-version": AgentFieldAgentVersion,
		"run-id": AgentFieldRunID, "parent-run-id": AgentFieldParentRunID,
		"task-id": AgentFieldTaskID, "trace-id": AgentFieldTraceID,
		"workflow": AgentFieldWorkflow, "step": AgentFieldStep, "turn": AgentFieldTurn,
		"budget-scope": AgentFieldBudgetScope, "ledger-id": AgentFieldBudgetScope,
		"budget-usd": AgentFieldBudgetUSD, "budget-remaining-usd": AgentFieldBudgetRemainingUSD,
		"max-cost-usd": AgentFieldMaxCostUSD, "attempt-id": AgentFieldAttemptID,
		"deadline-ms": AgentFieldDeadlineMs, "route-hint": AgentFieldRouteHint,
		"require": AgentFieldRequire, "prev-outcome": AgentFieldPrevOutcome,
		"env": AgentFieldEnv, "framework": AgentFieldFramework,
		"user-id": AgentFieldUserID, "workspace-id": AgentFieldWorkspaceID,
	}
	for suffix, want := range cases {
		got, ok := AgentFieldBySuffix(suffix)
		if !ok || got != want {
			t.Errorf("AgentFieldBySuffix(%q) = %d, %v; want %d", suffix, got, ok, want)
		}
		key := strings.ReplaceAll(suffix, "-", "_")
		if got, ok := AgentFieldByKey(key); !ok || got != want {
			t.Errorf("AgentFieldByKey(%q) = %d, %v; want %d", key, got, ok, want)
		}
	}
	for _, unknown := range []string{"session-id", "user", "tags", "metadata", "Run-Id", ""} {
		if _, ok := AgentFieldBySuffix(unknown); ok {
			t.Errorf("AgentFieldBySuffix(%q) matched; it is not an agent field", unknown)
		}
	}
	if len(AgentFieldSuffixes) != AgentFieldCount {
		t.Errorf("AgentFieldSuffixes has %d entries, want %d", len(AgentFieldSuffixes), AgentFieldCount)
	}
}

func TestAgentHeaderInvalidValues(t *testing.T) {
	long := strings.Repeat("a", 129)
	bad := []struct {
		field int
		value string
	}{
		{AgentFieldRunID, ""}, {AgentFieldRunID, long}, {AgentFieldRunID, "run 1"}, {AgentFieldRunID, "run#1"},
		{AgentFieldAgentID, "a\nb"}, {AgentFieldWorkflow, "wf?x"},
		{AgentFieldStep, "thinking"}, {AgentFieldStep, "ACT"},
		{AgentFieldTurn, "-1"}, {AgentFieldTurn, "100001"}, {AgentFieldTurn, "1.5"}, {AgentFieldTurn, "x"},
		{AgentFieldBudgetUSD, "-0.1"}, {AgentFieldBudgetUSD, "1000000.01"}, {AgentFieldBudgetUSD, "1e3"},
		{AgentFieldBudgetUSD, "NaN"}, {AgentFieldBudgetUSD, "Inf"}, {AgentFieldMaxCostUSD, "0x10"},
		{AgentFieldBudgetRemainingUSD, "."}, {AgentFieldBudgetRemainingUSD, "1.2.3"},
		{AgentFieldDeadlineMs, "0"}, {AgentFieldDeadlineMs, "3600001"}, {AgentFieldDeadlineMs, "10.5"},
		{AgentFieldRequire, "tools,teleport"}, {AgentFieldRequire, ""}, {AgentFieldRequire, ",,"},
		{AgentFieldRouteHint, "fast"}, {AgentFieldPrevOutcome, "failed"},
		{AgentFieldEnv, strings.Repeat("e", 33)}, {AgentFieldEnv, "prod env"},
		{AgentFieldFramework, strings.Repeat("f", 33)},
	}
	for _, c := range bad {
		var a AgentContext
		fe := a.SetAgentField(c.field, c.value)
		if fe == nil {
			t.Errorf("SetAgentField(%s, %q) accepted the value", AgentFieldSuffixes[c.field], c.value)
			continue
		}
		if fe.Field != c.field || fe.Reason == "" {
			t.Errorf("SetAgentField(%s, %q) error = %+v", AgentFieldSuffixes[c.field], c.value, fe)
		}
	}
}

func TestAgentHeadersValidValues(t *testing.T) {
	var a AgentContext
	set := func(field int, value string) {
		t.Helper()
		if err := a.SetAgentField(field, value); err != nil {
			t.Fatalf("SetAgentField(%s, %q): %v", AgentFieldSuffixes[field], value, err)
		}
	}
	id128 := strings.Repeat("r", 128)
	set(AgentFieldRunID, id128)
	set(AgentFieldAgentID, "agent.writer@v2:main/1-a_b")
	set(AgentFieldStep, "verify")
	set(AgentFieldTurn, "0")
	set(AgentFieldBudgetUSD, "1000000")
	set(AgentFieldMaxCostUSD, "0.25")
	set(AgentFieldDeadlineMs, "3600000")
	set(AgentFieldRequire, "tools, json_schema,tools,long_context")
	set(AgentFieldRouteHint, "latency")
	set(AgentFieldPrevOutcome, "verify_failed")
	set(AgentFieldEnv, "staging")
	set(AgentFieldFramework, "langgraph")
	set(AgentFieldWorkflow, "triage-flow")
	if a.RunID != id128 || a.AgentID != "agent.writer@v2:main/1-a_b" || a.Step != StepVerify || a.Workflow != "triage-flow" {
		t.Errorf("ids and step not stored: %+v", a)
	}
	if a.Turn == nil || *a.Turn != 0 {
		t.Errorf("turn 0 must be stored as a real turn, got %v", a.Turn)
	}
	if a.BudgetUSD == nil || *a.BudgetUSD != 1e6 || a.MaxCostUSD == nil || *a.MaxCostUSD != 0.25 {
		t.Errorf("USD values not stored: %v %v", a.BudgetUSD, a.MaxCostUSD)
	}
	if a.DeadlineMs != 3600000 || a.RouteHint != "latency" || a.PrevOutcome != "verify_failed" {
		t.Errorf("deadline, hint or outcome not stored: %+v", a)
	}
	if strings.Join(a.Require, ",") != "tools,json_schema,long_context" {
		t.Errorf("require = %v, want trimmed and deduplicated in order", a.Require)
	}
	if a.Env != "staging" || a.Framework != "langgraph" {
		t.Errorf("env or framework not stored: %+v", a)
	}
}

func TestAttributionLimitsTags(t *testing.T) {
	tags, err := ParseAgentTags(" a, b ,a,team:x/y.z-1")
	if err != nil || strings.Join(tags, "|") != "a|b|team:x/y.z-1" {
		t.Fatalf("ParseAgentTags = %v, %v", tags, err)
	}
	if _, err := ParseAgentTags(strings.Repeat("t", 64)); err != nil {
		t.Errorf("a 64-character tag is valid: %v", err)
	}
	eleven := "1,2,3,4,5,6,7,8,9,10,11"
	for _, bad := range []string{strings.Repeat("t", 65), "a,,b", "", "a b", "tag@x", eleven} {
		if _, err := ParseAgentTags(bad); err == nil {
			t.Errorf("ParseAgentTags(%q) accepted", bad)
		}
	}
	if err := ValidAgentTags([]string{"a", "b"}); err != nil {
		t.Errorf("ValidAgentTags: %v", err)
	}
	if err := ValidAgentTags(strings.Split(eleven, ",")); err == nil {
		t.Error("ValidAgentTags accepted 11 tags")
	}
}

func TestAttributionLimitsTruncate(t *testing.T) {
	if got := TruncateRunes("héllo", 3); got != "hél" {
		t.Errorf("TruncateRunes = %q", got)
	}
	if got := TruncateRunes("abc", 3); got != "abc" {
		t.Errorf("TruncateRunes kept %q", got)
	}
	s := strings.Repeat("é", 300)
	if got := TruncateRunes(s, 256); len([]rune(got)) != 256 {
		t.Errorf("TruncateRunes kept %d runes", len([]rune(got)))
	}
}

func TestValidAgentIDMatchesTheContractRegex(t *testing.T) {
	// ^[A-Za-z0-9._:@/-]{1,128}$
	for _, ok := range []string{"a", "A9._:@/-", strings.Repeat("x", 128)} {
		if !ValidAgentID(ok) {
			t.Errorf("ValidAgentID(%q) = false", ok)
		}
	}
	for _, bad := range []string{"", strings.Repeat("x", 129), "a b", "a+b", "é", "a\x00"} {
		if ValidAgentID(bad) {
			t.Errorf("ValidAgentID(%q) = true", bad)
		}
	}
}
