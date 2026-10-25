package models

// The user-id rule at the field level. user-id accepts any printable value of 1
// to 255 bytes (valid UTF-8; every character a letter, mark, number,
// punctuation, symbol or the ASCII space); every other id field keeps
// ^[A-Za-z0-9._:@/-]{1,128}$.

import (
	"strings"
	"testing"
)

func TestAgentUserIDRule(t *testing.T) {
	for _, v := range []string{
		"jane+tag@example.com", "user 42", "Jane Doe", "{123e4567-e89b}", "#42 (admin)", "Zoë", "用户42", "a\ufffdb",
		strings.Repeat("u", 129), strings.Repeat("u", 255), strings.Repeat("é", 127) + "x",
	} {
		var a AgentContext
		if err := a.SetAgentField(AgentFieldUserID, v); err != nil {
			t.Errorf("user-id %q (%d bytes) refused: %v", v, len(v), err)
			continue
		}
		if a.UserID != v {
			t.Errorf("user-id %q stored as %q", v, a.UserID)
		}
	}
	for _, v := range []string{
		"", strings.Repeat("u", 256), strings.Repeat("é", 128), "a\tb", "a\nb", "a\rb", "a\x00b", "a\x7fb",
		"a\u0085b", "a\u00a0b", "a\u200bb", "a\u2028b", "\ufeffa", "a\xffb", "a\xc3",
	} {
		var a AgentContext
		err := a.SetAgentField(AgentFieldUserID, v)
		if err == nil {
			t.Errorf("user-id %q accepted", v)
			continue
		}
		if err.Field != AgentFieldUserID || !strings.Contains(err.Reason, "1 to 255 bytes") {
			t.Errorf("user-id %q: %v, want the stated rule", v, err)
		}
	}
	// The other id fields keep the id rule: they key Redis state.
	for _, f := range []int{AgentFieldAgentID, AgentFieldRunID, AgentFieldBudgetScope, AgentFieldWorkspaceID, AgentFieldAttemptID} {
		var a AgentContext
		if err := a.SetAgentField(f, "user 42"); err == nil {
			t.Errorf("%s accepted \"user 42\"", AgentFieldSuffixes[f])
		}
		if err := a.SetAgentField(f, strings.Repeat("u", 129)); err == nil {
			t.Errorf("%s accepted 129 characters", AgentFieldSuffixes[f])
		}
	}
}
