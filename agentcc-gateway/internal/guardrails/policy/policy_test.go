package policy

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestStore_RegisterAndGet(t *testing.T) {
	s := NewStore()
	thresh := 0.9
	s.Register("key_1", &config.KeyGuardrailPolicyConfig{
		Overrides: []config.KeyGuardrailOverride{
			{Name: "pii-detection", Action: "log", Threshold: &thresh},
			{Name: "prompt-injection", Disabled: true},
		},
	})

	p := s.Get("key_1")
	if p == nil {
		t.Fatal("expected policy for key_1")
	}
	if len(p.Overrides) != 2 {
		t.Fatalf("expected 2 overrides, got %d", len(p.Overrides))
	}

	pii := p.Overrides["pii-detection"]
	if pii.Action != "log" {
		t.Errorf("expected action 'log', got %q", pii.Action)
	}
	if pii.Threshold == nil || *pii.Threshold != 0.9 {
		t.Errorf("expected threshold 0.9, got %v", pii.Threshold)
	}

	inj := p.Overrides["prompt-injection"]
	if !inj.Disabled {
		t.Error("expected prompt-injection disabled")
	}
}

func TestStore_GetMissing(t *testing.T) {
	s := NewStore()
	if s.Get("nonexistent") != nil {
		t.Error("expected nil for missing key")
	}
}

func TestStore_GetNilStore(t *testing.T) {
	var s *Store
	if s.Get("key_1") != nil {
		t.Error("expected nil for nil store")
	}
}

func TestStore_RegisterNilConfig(t *testing.T) {
	s := NewStore()
	s.Register("key_1", nil)
	if s.Get("key_1") != nil {
		t.Error("nil config should not register policy")
	}
}

func TestStore_RegisterEmptyOverrides(t *testing.T) {
	s := NewStore()
	s.Register("key_1", &config.KeyGuardrailPolicyConfig{
		Overrides: []config.KeyGuardrailOverride{},
	})
	if s.Get("key_1") != nil {
		t.Error("empty overrides should not register policy")
	}
}

func TestParseRequestPolicy(t *testing.T) {
	tests := []struct {
		input    string
		expected RequestPolicy
		valid    bool
	}{
		{"log-only", RequestPolicyLogOnly, true},
		{"disabled", RequestPolicyDisabled, true},
		{"strict", RequestPolicyStrict, true},
		{"", RequestPolicyNone, true},
		{"invalid", RequestPolicyNone, false},
		{"LOG-ONLY", RequestPolicyNone, false}, // case-sensitive
	}

	for _, tt := range tests {
		rp, valid := ParseRequestPolicy(tt.input)
		if valid != tt.valid {
			t.Errorf("ParseRequestPolicy(%q) valid = %v, want %v", tt.input, valid, tt.valid)
		}
		if rp != tt.expected {
			t.Errorf("ParseRequestPolicy(%q) = %v, want %v", tt.input, rp, tt.expected)
		}
	}
}

func TestOverride_ThresholdZero(t *testing.T) {
	s := NewStore()
	thresh := 0.0 // 0.0 is a valid threshold (strict mode)
	s.Register("key_1", &config.KeyGuardrailPolicyConfig{
		Overrides: []config.KeyGuardrailOverride{
			{Name: "content-moderation", Threshold: &thresh},
		},
	})

	p := s.Get("key_1")
	if p == nil {
		t.Fatal("expected policy")
	}
	ov := p.Overrides["content-moderation"]
	if ov.Threshold == nil {
		t.Fatal("threshold should not be nil")
	}
	if *ov.Threshold != 0.0 {
		t.Errorf("expected threshold 0.0, got %f", *ov.Threshold)
	}
}
