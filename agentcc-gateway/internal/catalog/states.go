package catalog

import (
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode"
)

// Model availability states.
const (
	StateActive     = "active"
	StateDegraded   = "degraded"
	StateDeprecated = "deprecated"
	StateBlocked    = "blocked"
)

// deprecationWindow is how long before its expiration date a model counts as
// deprecated.
const deprecationWindow = 14 * 24 * time.Hour

// Block is one blocked model id or glob and why.
type Block struct {
	Model  string `json:"model"`
	Reason string `json:"reason"`
	// Source is "config" (catalog.blocked_models) or "runtime"
	// (PUT /-/catalog/blocks, persisted in catalog.snapshot_dir).
	Source string `json:"source"`
}

// deriveState is a model's state, in precedence order: blocked (by config or
// at runtime), deprecated (it expires within deprecationWindow or already
// has), degraded (SetModelState), else active.
func deriveState(m *Model, blocked func(id string) (string, bool), degradedReason string, now time.Time) (state, reason string) {
	if why, ok := blocked(m.ID); ok {
		return StateBlocked, why
	}
	if m.ExpirationDate != "" {
		if exp, err := time.Parse(time.DateOnly, m.ExpirationDate); err == nil && !now.Before(exp.Add(-deprecationWindow)) {
			return StateDeprecated, "expires:" + m.ExpirationDate
		}
	}
	if degradedReason != "" {
		return StateDegraded, degradedReason
	}
	return StateActive, ""
}

// withStatesLocked is snap with every model's state derived as of now.
func (s *Store) withStatesLocked(snap *Snapshot, now time.Time) *Snapshot {
	out := *snap
	out.Models = make(map[string]*Model, len(snap.Models))
	for id, m := range snap.Models {
		mc := *m
		mc.State, mc.StateReason = deriveState(m, s.blockedLocked, s.degraded[id], now)
		out.Models[id] = &mc
	}
	return &out
}

// blockedLocked reports whether id is blocked and why: by a runtime block
// first, then by config.
func (s *Store) blockedLocked(id string) (string, bool) {
	for pattern, reason := range s.runtimeBlocks {
		if globMatch(pattern, id) {
			if reason == "" {
				reason = "blocked"
			}
			return reason, true
		}
	}
	for _, pattern := range s.opts.BlockedModels {
		if globMatch(pattern, id) {
			return "catalog.blocked_models", true
		}
	}
	return "", false
}

// SetModelState sets a runtime state on model id: degraded (with reason),
// or active to clear it. Blocking goes through SetBlocks, and deprecation
// follows the expiration date.
func (s *Store) SetModelState(id, state, reason string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch state {
	case StateDegraded:
		if reason == "" {
			reason = StateDegraded
		}
		s.degraded[id] = reason
	case StateActive:
		delete(s.degraded, id)
	default:
		return fmt.Errorf("catalog: SetModelState takes %q or %q, not %q", StateDegraded, StateActive, state)
	}
	s.publishLocked("", OriginRestored, nil)
	return nil
}

// SetBlocks replaces the runtime blocks with models (ids or globs), all for
// reason, persists them in the snapshot directory, and returns every block
// now in force.
func (s *Store) SetBlocks(models []string, reason string) ([]Block, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	next := make(map[string]string, len(models))
	for _, m := range models {
		if m = strings.TrimSpace(m); m != "" {
			next[m] = reason
		}
	}
	if s.persist != nil {
		if err := s.persist.saveBlocks(next); err != nil {
			return nil, err
		}
	}
	s.runtimeBlocks = next
	s.publishLocked("", OriginRestored, nil)
	return s.blocksLocked(), nil
}

// Blocks returns every block in force, config first, each list sorted.
func (s *Store) Blocks() []Block {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.blocksLocked()
}

func (s *Store) blocksLocked() []Block {
	out := make([]Block, 0, len(s.opts.BlockedModels)+len(s.runtimeBlocks))
	cfg := append([]string(nil), s.opts.BlockedModels...)
	sort.Strings(cfg)
	for _, m := range cfg {
		out = append(out, Block{Model: m, Reason: "catalog.blocked_models", Source: "config"})
	}
	runtime := make([]string, 0, len(s.runtimeBlocks))
	for m := range s.runtimeBlocks {
		runtime = append(runtime, m)
	}
	sort.Strings(runtime)
	for _, m := range runtime {
		out = append(out, Block{Model: m, Reason: s.runtimeBlocks[m], Source: "runtime"})
	}
	return out
}

// RefreshStates re-derives every model's state as of now, so a model whose
// expiration date came within the window turns deprecated without waiting
// for its catalog to change.
func (s *Store) RefreshStates() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.publishLocked("", OriginRestored, nil)
}

// isPreview reports whether a model id or name marks a preview: a word
// "preview", "exp", "experimental" or "beta", alone or followed by digits
// ("gemini-exp-1206", "o1-preview", "beta2"). "expert" does not count.
func isPreview(id, name string) bool {
	for _, word := range strings.FieldsFunc(strings.ToLower(id+" "+name), func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	}) {
		stem := strings.TrimRightFunc(word, unicode.IsDigit)
		switch stem {
		case "preview", "exp", "experimental", "beta":
			return true
		}
	}
	return false
}

// globMatch matches s against pattern, where "*" matches any run of
// characters, "/" included.
func globMatch(pattern, s string) bool {
	parts := strings.Split(pattern, "*")
	if len(parts) == 1 {
		return pattern == s
	}
	if !strings.HasPrefix(s, parts[0]) {
		return false
	}
	s = s[len(parts[0]):]
	last := parts[len(parts)-1]
	for _, p := range parts[1 : len(parts)-1] {
		i := strings.Index(s, p)
		if i < 0 {
			return false
		}
		s = s[i+len(p):]
	}
	return strings.HasSuffix(s, last)
}
