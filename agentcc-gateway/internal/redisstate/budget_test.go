package redisstate

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Unit tests (no Redis)
// ---------------------------------------------------------------------------

func TestBudgetStore_NilClient_RecordSpend(t *testing.T) {
	bs := NewBudgetStore(nil, "test:budget:")
	total, ok := bs.RecordSpend("org1", "org", "", "monthly", "gpt-4o", 1.50)
	if ok {
		t.Fatal("expected ok=false with nil client")
	}
	if total != -1 {
		t.Errorf("total = %f, want -1", total)
	}
}

func TestBudgetStore_NilClient_GetSpend(t *testing.T) {
	bs := NewBudgetStore(nil, "test:budget:")
	_, _, ok := bs.GetSpend("org1", "org", "", "monthly")
	if ok {
		t.Fatal("expected ok=false with nil client")
	}
}

func TestBudgetStore_NilClient_Available(t *testing.T) {
	bs := NewBudgetStore(nil, "test:budget:")
	if bs.Available() {
		t.Fatal("expected not available with nil client")
	}
}

func TestBudgetStore_UnavailableClient(t *testing.T) {
	client := &Client{breaker: newCircuitBreaker(1, 10*time.Second)}
	client.breaker.recordFailure()

	bs := NewBudgetStore(client, "test:budget:")
	if bs.Available() {
		t.Fatal("expected not available with open circuit")
	}
	total, ok := bs.RecordSpend("org1", "org", "", "monthly", "", 1.0)
	if ok || total != -1 {
		t.Errorf("expected (-1, false), got (%f, %v)", total, ok)
	}
}

func TestBudgetTTL(t *testing.T) {
	cases := []struct {
		period string
		minH   float64
		maxH   float64
	}{
		{"daily", 47, 49},
		{"weekly", 191, 193},
		{"monthly", 839, 841},
		{"total", 0, 0},
	}
	for _, tc := range cases {
		ttl := budgetTTL(tc.period)
		hours := ttl.Hours()
		if tc.period == "total" {
			if ttl != 0 {
				t.Errorf("period=%s: ttl=%v, want 0", tc.period, ttl)
			}
			continue
		}
		if hours < tc.minH || hours > tc.maxH {
			t.Errorf("period=%s: ttl=%.1fh, want [%.0f, %.0f]", tc.period, hours, tc.minH, tc.maxH)
		}
	}
}

func TestMicrodollarConversion(t *testing.T) {
	usd := 1.50
	micros := usdToMicros(usd)
	if micros != 1_500_000 {
		t.Errorf("usdToMicros(1.50) = %d, want 1500000", micros)
	}

	back := microsToUSD(micros)
	if back != usd {
		t.Errorf("microsToUSD(%d) = %f, want %f", micros, back, usd)
	}

	// Edge case: very small amounts.
	micros = usdToMicros(0.000001)
	if micros != 1 {
		t.Errorf("usdToMicros(0.000001) = %d, want 1", micros)
	}
}

// ---------------------------------------------------------------------------
// Integration tests (require real Redis)
// ---------------------------------------------------------------------------

func TestBudgetStore_Redis_RecordAndGetSpend(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	bs := NewBudgetStore(client, "test:budget:"+ts+":")

	org := "test-org-" + ts

	// Record some spend.
	total, ok := bs.RecordSpend(org, "org", "", "daily", "gpt-4o", 5.25)
	if !ok {
		t.Fatal("expected ok=true for RecordSpend")
	}
	if total < 5.24 || total > 5.26 {
		t.Errorf("total after first record = %f, want ~5.25", total)
	}

	// Record more spend.
	total, ok = bs.RecordSpend(org, "org", "", "daily", "gpt-4o", 3.75)
	if !ok {
		t.Fatal("expected ok=true for second RecordSpend")
	}
	if total < 8.99 || total > 9.01 {
		t.Errorf("total after second record = %f, want ~9.00", total)
	}

	// Record spend for a different model.
	_, ok = bs.RecordSpend(org, "org", "", "daily", "claude-3", 2.00)
	if !ok {
		t.Fatal("expected ok=true for third RecordSpend")
	}

	// Get spend and verify.
	totalSpend, modelSpend, ok := bs.GetSpend(org, "org", "", "daily")
	if !ok {
		t.Fatal("expected ok=true for GetSpend")
	}
	if totalSpend < 10.99 || totalSpend > 11.01 {
		t.Errorf("totalSpend = %f, want ~11.00", totalSpend)
	}
	if gpt4o, ok := modelSpend["gpt-4o"]; !ok || gpt4o < 8.99 || gpt4o > 9.01 {
		t.Errorf("modelSpend[gpt-4o] = %f, want ~9.00", gpt4o)
	}
	if claude, ok := modelSpend["claude-3"]; !ok || claude < 1.99 || claude > 2.01 {
		t.Errorf("modelSpend[claude-3] = %f, want ~2.00", claude)
	}
}

func TestBudgetStore_Redis_SeedSpend(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	bs := NewBudgetStore(client, "test:budget:seed:"+ts+":")

	org := "seed-org-" + ts
	modelSpend := map[string]float64{
		"gpt-4o":  15.50,
		"claude-3": 8.25,
	}

	ok := bs.SeedSpend(org, "org", "", "monthly", 23.75, modelSpend)
	if !ok {
		t.Fatal("expected SeedSpend to succeed")
	}

	// Verify seeded data.
	total, models, ok := bs.GetSpend(org, "org", "", "monthly")
	if !ok {
		t.Fatal("expected GetSpend to succeed after seed")
	}
	if total < 23.74 || total > 23.76 {
		t.Errorf("seeded total = %f, want ~23.75", total)
	}
	if gpt, ok := models["gpt-4o"]; !ok || gpt < 15.49 || gpt > 15.51 {
		t.Errorf("seeded gpt-4o = %f, want ~15.50", gpt)
	}
}

func TestBudgetStore_Redis_PerKeySpend(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	bs := NewBudgetStore(client, "test:budget:perkey:"+ts+":")

	org := "perkey-org-" + ts

	// Record spend against key-1 and key-2.
	bs.RecordSpend(org, "key", "key-1", "monthly", "gpt-4o", 10.00)
	bs.RecordSpend(org, "key", "key-2", "monthly", "gpt-4o", 20.00)
	bs.RecordSpend(org, "key", "key-1", "monthly", "gpt-4o", 5.00)

	// Verify key-1.
	total1, _, ok := bs.GetSpend(org, "key", "key-1", "monthly")
	if !ok {
		t.Fatal("expected ok for key-1")
	}
	if total1 < 14.99 || total1 > 15.01 {
		t.Errorf("key-1 total = %f, want ~15.00", total1)
	}

	// Verify key-2.
	total2, _, ok := bs.GetSpend(org, "key", "key-2", "monthly")
	if !ok {
		t.Fatal("expected ok for key-2")
	}
	if total2 < 19.99 || total2 > 20.01 {
		t.Errorf("key-2 total = %f, want ~20.00", total2)
	}
}

func TestBudgetStore_Redis_EmptyKeyReturnsZero(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	bs := NewBudgetStore(client, "test:budget:empty:"+ts+":")

	total, models, ok := bs.GetSpend("nonexistent-org", "org", "", "monthly")
	if !ok {
		t.Fatal("expected ok=true for nonexistent key")
	}
	if total != 0 {
		t.Errorf("total = %f, want 0", total)
	}
	if len(models) != 0 {
		t.Errorf("models = %v, want empty", models)
	}
}
