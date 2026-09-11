package rotation

import (
	"context"
	"fmt"
	"testing"
	"time"
)

func TestNewManager(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	if m == nil {
		t.Fatal("expected non-nil manager")
	}
}

func TestRegisterProvider(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")

	state, err := m.GetStatus("openai")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if state.Status != StatusIdle {
		t.Fatalf("expected idle, got %s", state.Status)
	}
	if state.Primary != "sk-old" {
		t.Fatalf("expected sk-old, got %s", state.Primary)
	}
}

func TestStartRotation(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")

	err := m.StartRotation("openai", "sk-new")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	state, _ := m.GetStatus("openai")
	if state.Status != StatusPending {
		t.Fatalf("expected pending, got %s", state.Status)
	}
	if state.PendingKey != "sk-new" {
		t.Fatalf("expected sk-new pending, got %s", state.PendingKey)
	}
}

func TestStartRotation_UnregisteredProvider(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	err := m.StartRotation("unknown", "sk-new")
	if err == nil {
		t.Fatal("expected error for unregistered provider")
	}
}

func TestStartRotation_AlreadyPending(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")
	m.StartRotation("openai", "sk-new1")

	err := m.StartRotation("openai", "sk-new2")
	if err == nil {
		t.Fatal("expected error when rotation already pending")
	}
}

func TestStartRotation_EmptyKey(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")

	err := m.StartRotation("openai", "")
	if err == nil {
		t.Fatal("expected error for empty new key")
	}
}

func TestPromote(t *testing.T) {
	var rotatedKey string
	onRotate := func(providerID, newKey string) {
		rotatedKey = newKey
	}

	m := NewManager(100*time.Millisecond, onRotate)
	m.RegisterProvider("openai", "sk-old")
	m.StartRotation("openai", "sk-new")

	err := m.Promote("openai")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if rotatedKey != "sk-new" {
		t.Fatalf("expected onRotate called with sk-new, got %s", rotatedKey)
	}

	state, _ := m.GetStatus("openai")
	if state.Status != StatusDraining {
		t.Fatalf("expected draining, got %s", state.Status)
	}
	if state.Primary != "sk-new" {
		t.Fatalf("expected sk-new as primary, got %s", state.Primary)
	}
	if state.OldKey != "sk-old" {
		t.Fatalf("expected sk-old as old key, got %s", state.OldKey)
	}
	if state.DrainUntil == nil {
		t.Fatal("expected drain_until to be set")
	}

	// Wait for drain to complete.
	time.Sleep(200 * time.Millisecond)

	state2, _ := m.GetStatus("openai")
	if state2.Status != StatusIdle {
		t.Fatalf("expected idle after drain, got %s", state2.Status)
	}
	if state2.OldKey != "" {
		t.Fatalf("expected old key cleared after drain, got %s", state2.OldKey)
	}
}

func TestPromote_NoPending(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")

	err := m.Promote("openai")
	if err == nil {
		t.Fatal("expected error when no pending key")
	}
}

func TestRollback(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")
	m.StartRotation("openai", "sk-new")

	err := m.Rollback("openai")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	state, _ := m.GetStatus("openai")
	if state.Status != StatusIdle {
		t.Fatalf("expected idle after rollback, got %s", state.Status)
	}
	if state.PendingKey != "" {
		t.Fatal("expected pending key cleared")
	}
	if state.Primary != "sk-old" {
		t.Fatalf("expected primary unchanged, got %s", state.Primary)
	}
}

func TestRollback_NoActiveRotation(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")

	err := m.Rollback("openai")
	if err == nil {
		t.Fatal("expected error when no active rotation")
	}
}

func TestValidate_Success(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")
	m.StartRotation("openai", "sk-new")

	err := m.Validate(context.Background(), "openai", func(ctx context.Context, key string) error {
		if key != "sk-new" {
			t.Fatalf("validate called with wrong key: %s", key)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestValidate_Failure(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")
	m.StartRotation("openai", "sk-bad")

	err := m.Validate(context.Background(), "openai", func(ctx context.Context, key string) error {
		return fmt.Errorf("invalid key")
	})
	if err == nil {
		t.Fatal("expected validation error")
	}
}

func TestValidate_NoPending(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-old")

	err := m.Validate(context.Background(), "openai", func(ctx context.Context, key string) error {
		return nil
	})
	if err == nil {
		t.Fatal("expected error when no pending key")
	}
}

func TestGetActiveKey(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProvider("openai", "sk-active")

	key := m.GetActiveKey("openai")
	if key != "sk-active" {
		t.Fatalf("expected sk-active, got %s", key)
	}

	// Unknown provider returns empty.
	if m.GetActiveKey("unknown") != "" {
		t.Fatal("expected empty for unknown provider")
	}
}

func TestMaskedState(t *testing.T) {
	state := &KeyState{
		Primary:    "sk-agentcc-abcdef123456",
		PendingKey: "sk-new-key-value",
		OldKey:     "",
	}

	masked := state.MaskedState()
	if masked.Primary == state.Primary {
		t.Fatal("primary should be masked")
	}
	if masked.Primary != "sk-a...3456" {
		t.Fatalf("unexpected mask format: %s", masked.Primary)
	}
	if masked.PendingKey != "sk-n...alue" {
		t.Fatalf("unexpected mask format: %s", masked.PendingKey)
	}
	if masked.OldKey != "" {
		t.Fatal("empty key should stay empty when masked")
	}
}

func TestMaskKey(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"", ""},
		{"short", "***"},
		{"12345678", "***"},
		{"sk-agentcc-abcdef123456", "sk-a...3456"},
		{"longer-key-value-here", "long...here"},
	}

	for _, tt := range tests {
		got := maskKey(tt.input)
		if got != tt.want {
			t.Errorf("maskKey(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}
