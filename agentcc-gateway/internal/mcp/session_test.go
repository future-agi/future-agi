package mcp

import (
	"testing"
	"time"
)

func TestSessionManagerCreate(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	s := sm.Create(Implementation{Name: "test", Version: "1.0"}, ClientCapabilities{})
	if s.ID == "" {
		t.Fatal("expected non-empty session ID")
	}
	if s.ClientInfo.Name != "test" {
		t.Fatalf("expected client name 'test', got %s", s.ClientInfo.Name)
	}
}

func TestSessionManagerGetValid(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	s := sm.Create(Implementation{Name: "test", Version: "1.0"}, ClientCapabilities{})

	got := sm.Get(s.ID)
	if got == nil {
		t.Fatal("expected to find session")
	}
	if got.ID != s.ID {
		t.Fatalf("session ID mismatch")
	}
}

func TestSessionManagerGetExpired(t *testing.T) {
	sm := NewSessionManager(1 * time.Millisecond)
	defer sm.Close()

	s := sm.Create(Implementation{Name: "test", Version: "1.0"}, ClientCapabilities{})

	// Wait for expiry.
	time.Sleep(5 * time.Millisecond)

	got := sm.Get(s.ID)
	if got != nil {
		t.Fatal("expected expired session to return nil")
	}
}

func TestSessionManagerGetNotFound(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	got := sm.Get("nonexistent")
	if got != nil {
		t.Fatal("expected nil for non-existent session")
	}
}

func TestSessionManagerDelete(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	s := sm.Create(Implementation{Name: "test", Version: "1.0"}, ClientCapabilities{})
	sm.Delete(s.ID)

	got := sm.Get(s.ID)
	if got != nil {
		t.Fatal("expected deleted session to return nil")
	}
}

func TestSessionManagerCount(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	sm.Create(Implementation{Name: "a", Version: "1"}, ClientCapabilities{})
	sm.Create(Implementation{Name: "b", Version: "1"}, ClientCapabilities{})
	sm.Create(Implementation{Name: "c", Version: "1"}, ClientCapabilities{})

	if sm.Count() != 3 {
		t.Fatalf("expected 3 sessions, got %d", sm.Count())
	}
}

func TestSessionTouch(t *testing.T) {
	sm := NewSessionManager(50 * time.Millisecond)
	defer sm.Close()

	s := sm.Create(Implementation{Name: "test", Version: "1.0"}, ClientCapabilities{})

	// Keep touching to prevent expiry.
	for i := 0; i < 5; i++ {
		time.Sleep(20 * time.Millisecond)
		got := sm.Get(s.ID) // Get calls Touch
		if got == nil {
			t.Fatalf("session expired after %d touches", i)
		}
	}
}

func TestSessionIdleSince(t *testing.T) {
	s := &Session{
		CreatedAt: time.Now(),
	}

	// No touch yet — should use CreatedAt.
	idle := s.IdleSince()
	if idle < 0 {
		t.Fatal("idle should be non-negative")
	}

	time.Sleep(5 * time.Millisecond)
	s.Touch()

	idle2 := s.IdleSince()
	if idle2 > 10*time.Millisecond {
		t.Fatalf("expected small idle after touch, got %v", idle2)
	}
}

func TestGenerateSessionID(t *testing.T) {
	ids := make(map[string]bool)
	for i := 0; i < 100; i++ {
		id := generateSessionID()
		if len(id) != 32 { // 16 bytes hex = 32 chars
			t.Fatalf("expected 32 char ID, got %d: %s", len(id), id)
		}
		if ids[id] {
			t.Fatal("duplicate session ID")
		}
		ids[id] = true
	}
}
