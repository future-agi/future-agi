package catalog

import (
	"testing"
	"time"
)

func TestStateTransitions(t *testing.T) {
	clock := newFakeClock() // 2026-10-15
	inDays := func(d int) string { return clock.Now().AddDate(0, 0, d).Format(time.DateOnly) }

	type step struct {
		name       string
		apply      func(s *Store)
		state      string
		reason     string
		expiration string
	}
	for _, tc := range []step{
		{name: "no signal is active", state: StateActive},
		{name: "expiring in 20 days is still active", expiration: inDays(20), state: StateActive},
		{name: "expiring in 14 days is deprecated", expiration: inDays(14), state: StateDeprecated, reason: "expires:" + inDays(14)},
		{name: "expiring in 13 days is deprecated", expiration: inDays(13), state: StateDeprecated, reason: "expires:" + inDays(13)},
		{name: "expired is deprecated", expiration: inDays(-3), state: StateDeprecated, reason: "expires:" + inDays(-3)},
		{name: "degraded", apply: func(s *Store) { mustSetState(t, s, StateDegraded, "error rate 40%") },
			state: StateDegraded, reason: "error rate 40%"},
		{name: "degraded then active again", apply: func(s *Store) {
			mustSetState(t, s, StateDegraded, "x")
			mustSetState(t, s, StateActive, "")
		}, state: StateActive},
		{name: "deprecated outranks degraded", expiration: inDays(1),
			apply: func(s *Store) { mustSetState(t, s, StateDegraded, "x") }, state: StateDeprecated, reason: "expires:" + inDays(1)},
		{name: "config block", apply: func(s *Store) {
			mustConfigure(t, s, Options{BlockedModels: []string{"vendor/*"}, Now: clock.Now})
		}, state: StateBlocked, reason: "catalog.blocked_models"},
		{name: "runtime block outranks everything", expiration: inDays(1), apply: func(s *Store) {
			mustSetState(t, s, StateDegraded, "x")
			if _, err := s.SetBlocks([]string{"vendor/model"}, "abuse"); err != nil {
				t.Fatal(err)
			}
		}, state: StateBlocked, reason: "abuse"},
		{name: "unblocked returns to its derived state", apply: func(s *Store) {
			mustBlock(t, s, []string{"vendor/model"}, "abuse")
			mustBlock(t, s, nil, "")
		}, state: StateActive},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := NewStore()
			mustConfigure(t, s, Options{Now: clock.Now})
			s.Put(&Snapshot{ProviderID: "p", Source: OriginLive, Models: map[string]*Model{
				"vendor/model": {ID: "vendor/model", InputPerToken: 1e-6, ExpirationDate: tc.expiration},
			}})
			id := s.Load().ID()
			if tc.apply != nil {
				tc.apply(s)
			}
			m, _ := s.Load().Get("p", "vendor/model")
			if m.State != tc.state || m.StateReason != tc.reason {
				t.Fatalf("state = %q (%q), want %q (%q)", m.State, m.StateReason, tc.state, tc.reason)
			}
			if s.Load().ID() != id {
				t.Fatal("a state change must not change the catalog's snapshot id")
			}
		})
	}
}

func TestDeprecationFollowsTheClock(t *testing.T) {
	clock := newFakeClock()
	s := NewStore()
	mustConfigure(t, s, Options{Now: clock.Now})
	s.Put(&Snapshot{ProviderID: "p", Models: map[string]*Model{
		"vendor/model": {ID: "vendor/model", ExpirationDate: clock.Now().AddDate(0, 0, 16).Format(time.DateOnly)},
	}})
	if m, _ := s.Load().Get("p", "vendor/model"); m.State != StateActive {
		t.Fatalf("state = %s, want active 16 days out", m.State)
	}
	clock.Advance(3 * 24 * time.Hour)
	s.RefreshStates()
	if m, _ := s.Load().Get("p", "vendor/model"); m.State != StateDeprecated {
		t.Fatalf("state = %s, want deprecated 13 days out", m.State)
	}
}

func TestSetModelStateRejectsDerivedStates(t *testing.T) {
	s := NewStore()
	for _, state := range []string{StateBlocked, StateDeprecated, "gone"} {
		if err := s.SetModelState("m", state, ""); err == nil {
			t.Errorf("SetModelState(%q) must be refused", state)
		}
	}
}

func TestPreviewFlag(t *testing.T) {
	for id, want := range map[string]bool{
		"google/gemini-3.5-pro-preview":   true,
		"google/gemini-exp-1206":          true,
		"openai/o1-preview-2024":          true,
		"vendor/model-beta":               true,
		"vendor/model-beta2":              true,
		"mistralai/codestral-expert":      false,
		"anthropic/claude-sonnet-5.5":     false,
		"qwen/qwen3.7-flash:free":         false,
		"vendor/model experimental build": true,
	} {
		if got := isPreview(id, ""); got != want {
			t.Errorf("isPreview(%q) = %v, want %v", id, got, want)
		}
	}
	if !isPreview("vendor/model", "Model (Preview)") {
		t.Error("the name counts too")
	}
}

func TestGlobMatch(t *testing.T) {
	for _, tc := range []struct {
		pattern, s string
		want       bool
	}{
		{"vendor/model", "vendor/model", true},
		{"vendor/model", "vendor/model-2", false},
		{"vendor/*", "vendor/a/b", true},
		{"*:free", "qwen/q:free", true},
		{"google/gemini-3*", "google/gemini-3.5-pro", true},
		{"google/gemini-3*", "google/gemini-2.5", false},
		{"a*b*c", "a-b-c", true},
		{"a*a", "a", false},
	} {
		if got := globMatch(tc.pattern, tc.s); got != tc.want {
			t.Errorf("globMatch(%q, %q) = %v, want %v", tc.pattern, tc.s, got, tc.want)
		}
	}
}

func mustSetState(t *testing.T, s *Store, state, reason string) {
	t.Helper()
	if err := s.SetModelState("vendor/model", state, reason); err != nil {
		t.Fatal(err)
	}
}
