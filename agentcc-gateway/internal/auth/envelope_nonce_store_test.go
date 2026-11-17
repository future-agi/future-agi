package auth

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

// memNonceStore is a NonceStore two verifiers share in-process, standing in
// for the store replicas share.
type memNonceStore struct {
	mu   sync.Mutex
	seen map[string]int64
	err  error
}

func (s *memNonceStore) Consume(_ context.Context, id string, exp, _ int64) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.err != nil {
		return false, s.err
	}
	if _, ok := s.seen[id]; ok {
		return false, nil
	}
	if s.seen == nil {
		s.seen = map[string]int64{}
	}
	s.seen[id] = exp
	return true, nil
}

// Two replicas that share a nonce store accept an envelope once between
// them: the second replica refuses it as a replay.
func TestEnvelopeSharedNonceStoreRefusesReplayAcrossReplicas(t *testing.T) {
	c, _, grant, body, now := fe1Fixture()
	token := fe1Raw(c.fields(), fe1TestSecret)
	store := &memNonceStore{}
	first := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
	second := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
	first.SetNonceStore(store)
	second.SetNonceStore(store)
	if !first.Shared() || !second.Shared() {
		t.Fatal("a verifier with a nonce store reports itself shared")
	}
	if _, _, err := first.VerifyDelegated(token, grant, "POST", c.Route, body, now); err != nil {
		t.Fatalf("first replica refused a fresh envelope: %v", err)
	}
	if _, _, err := second.VerifyDelegated(token, grant, "POST", c.Route, body, now); err == nil {
		t.Fatal("second replica accepted the same envelope")
	}
	if _, _, err := first.VerifyDelegated(token, grant, "POST", c.Route, body, now); err == nil {
		t.Fatal("first replica accepted its own envelope twice")
	}
	if NewEnvelopeVerifier(fe1TestSecret, now).Shared() {
		t.Fatal("a verifier without a nonce store is not shared")
	}
}

// A shared store that cannot answer refuses the envelope.
func TestEnvelopeSharedNonceStoreFailsClosed(t *testing.T) {
	c, _, grant, body, now := fe1Fixture()
	token := fe1Raw(c.fields(), fe1TestSecret)
	var reasons []string
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute), func(reason string) { reasons = append(reasons, reason) })
	v.SetNonceStore(&memNonceStore{err: errors.New("store down")})
	if _, _, err := v.VerifyDelegated(token, grant, "POST", c.Route, body, now); err == nil {
		t.Fatal("an envelope was accepted while the nonce store could not answer")
	}
	if len(reasons) != 1 || reasons[0] != "nonce_store_unavailable" {
		t.Fatalf("rejection reasons = %v, want [nonce_store_unavailable]", reasons)
	}
}
