package redisstate

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Unit tests (no Redis)
// ---------------------------------------------------------------------------

func TestCreditStore_NilClient(t *testing.T) {
	cs := NewCreditStore(nil, "test:credits:")
	if cs.Available() {
		t.Fatal("expected not available with nil client")
	}

	bal, ok := cs.GetBalance("key1")
	if ok || bal != -1 {
		t.Errorf("GetBalance = (%d, %v), want (-1, false)", bal, ok)
	}

	bal, ok = cs.DeductMicros("key1", 100)
	if ok || bal != -1 {
		t.Errorf("DeductMicros = (%d, %v), want (-1, false)", bal, ok)
	}

	bal, ok = cs.AddMicros("key1", 100)
	if ok || bal != -1 {
		t.Errorf("AddMicros = (%d, %v), want (-1, false)", bal, ok)
	}

	ok = cs.SetBalance("key1", 100)
	if ok {
		t.Fatal("SetBalance should return false with nil client")
	}
}

func TestCreditStore_UnavailableClient(t *testing.T) {
	client := &Client{breaker: newCircuitBreaker(1, 10*time.Second)}
	client.breaker.recordFailure()

	cs := NewCreditStore(client, "test:credits:")
	if cs.Available() {
		t.Fatal("expected not available with open circuit")
	}

	bal, ok := cs.GetBalance("key1")
	if ok || bal != -1 {
		t.Errorf("GetBalance = (%d, %v), want (-1, false)", bal, ok)
	}
}

// ---------------------------------------------------------------------------
// Integration tests (require real Redis)
// ---------------------------------------------------------------------------

func TestCreditStore_Redis_SetAndGetBalance(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:"+ts+":")

	keyID := "key-" + ts

	// Set initial balance ($10 = 10,000,000 microdollars).
	ok := cs.SetBalance(keyID, 10_000_000)
	if !ok {
		t.Fatal("expected SetBalance to succeed")
	}

	bal, ok := cs.GetBalance(keyID)
	if !ok {
		t.Fatal("expected GetBalance to succeed")
	}
	if bal != 10_000_000 {
		t.Errorf("balance = %d, want 10000000", bal)
	}
}

func TestCreditStore_Redis_DeductMicros(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:deduct:"+ts+":")

	keyID := "key-deduct-" + ts

	// Set $10 balance.
	cs.SetBalance(keyID, 10_000_000)

	// Deduct $2.50 (2,500,000 micros).
	newBal, ok := cs.DeductMicros(keyID, 2_500_000)
	if !ok {
		t.Fatal("expected DeductMicros to succeed")
	}
	if newBal != 7_500_000 {
		t.Errorf("balance after deduct = %d, want 7500000", newBal)
	}

	// Verify via GetBalance.
	bal, ok := cs.GetBalance(keyID)
	if !ok {
		t.Fatal("expected GetBalance to succeed")
	}
	if bal != 7_500_000 {
		t.Errorf("GetBalance = %d, want 7500000", bal)
	}
}

func TestCreditStore_Redis_AddMicros(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:add:"+ts+":")

	keyID := "key-add-" + ts

	// Set $5 balance.
	cs.SetBalance(keyID, 5_000_000)

	// Add $3 (3,000,000 micros).
	newBal, ok := cs.AddMicros(keyID, 3_000_000)
	if !ok {
		t.Fatal("expected AddMicros to succeed")
	}
	if newBal != 8_000_000 {
		t.Errorf("balance after add = %d, want 8000000", newBal)
	}
}

func TestCreditStore_Redis_DeductBelowZero(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:belowzero:"+ts+":")

	keyID := "key-belowzero-" + ts

	// Set $1 balance.
	cs.SetBalance(keyID, 1_000_000)

	// Deduct $2 — the Lua script checks balance > 0, should still deduct
	// (returns negative balance via DECRBY).
	newBal, ok := cs.DeductMicros(keyID, 2_000_000)
	if !ok {
		t.Fatal("expected DeductMicros to succeed")
	}
	if newBal != -1_000_000 {
		t.Errorf("balance after over-deduct = %d, want -1000000", newBal)
	}
}

func TestCreditStore_Redis_DeductFromZeroBalance(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:zerodeduct:"+ts+":")

	keyID := "key-zero-" + ts

	// Set zero balance.
	cs.SetBalance(keyID, 0)

	// Deduct should be rejected by the Lua script (balance <= 0).
	newBal, ok := cs.DeductMicros(keyID, 1_000_000)
	if !ok {
		t.Fatal("expected DeductMicros to succeed (Lua returns -1)")
	}
	if newBal != -1 {
		t.Errorf("balance = %d, want -1 (rejected by Lua)", newBal)
	}
}

func TestCreditStore_Redis_ForceDeductMicros(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:forcededuct:"+ts+":")

	keyID := "key-force-" + ts

	// Set $5 balance.
	cs.SetBalance(keyID, 5_000_000)

	// Force-deduct $3 — should succeed.
	newBal, ok := cs.ForceDeductMicros(keyID, 3_000_000)
	if !ok {
		t.Fatal("expected ForceDeductMicros to succeed")
	}
	if newBal != 2_000_000 {
		t.Errorf("balance after force-deduct = %d, want 2000000", newBal)
	}

	// Force-deduct $5 more — should go negative (no balance check).
	newBal, ok = cs.ForceDeductMicros(keyID, 5_000_000)
	if !ok {
		t.Fatal("expected ForceDeductMicros to succeed even going negative")
	}
	if newBal != -3_000_000 {
		t.Errorf("balance after over-deduct = %d, want -3000000", newBal)
	}
}

func TestCreditStore_Redis_ForceDeductFromZeroBalance(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:forcezero:"+ts+":")

	keyID := "key-forcezero-" + ts

	// Set zero balance.
	cs.SetBalance(keyID, 0)

	// ForceDeduct should still deduct (unlike DeductMicros which returns -1).
	newBal, ok := cs.ForceDeductMicros(keyID, 1_000_000)
	if !ok {
		t.Fatal("expected ForceDeductMicros to succeed")
	}
	if newBal != -1_000_000 {
		t.Errorf("balance = %d, want -1000000", newBal)
	}
}

func TestCreditStore_Redis_SeedBalance(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:seed:"+ts+":")

	keyID := "key-seed-" + ts

	// First seed should succeed (key doesn't exist).
	ok := cs.SeedBalance(keyID, 10_000_000)
	if !ok {
		t.Fatal("expected first SeedBalance to succeed")
	}

	bal, ok := cs.GetBalance(keyID)
	if !ok {
		t.Fatal("expected GetBalance to succeed")
	}
	if bal != 10_000_000 {
		t.Errorf("balance = %d, want 10000000", bal)
	}

	// Second seed should be a no-op (SETNX fails, key exists).
	ok = cs.SeedBalance(keyID, 20_000_000)
	if ok {
		t.Fatal("expected second SeedBalance to return false (key exists)")
	}

	// Balance should remain unchanged.
	bal, ok = cs.GetBalance(keyID)
	if !ok {
		t.Fatal("expected GetBalance to succeed")
	}
	if bal != 10_000_000 {
		t.Errorf("balance after second seed = %d, want 10000000 (unchanged)", bal)
	}
}

func TestCreditStore_Redis_SeedDoesNotOverwriteTraffic(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:seedtraffic:"+ts+":")

	keyID := "key-seedtraffic-" + ts

	// Simulate real traffic setting a balance via SetBalance (unconditional).
	cs.SetBalance(keyID, 8_000_000)

	// Seed should not overwrite the traffic-set balance.
	ok := cs.SeedBalance(keyID, 15_000_000)
	if ok {
		t.Fatal("expected SeedBalance to return false when key already exists from traffic")
	}

	bal, ok := cs.GetBalance(keyID)
	if !ok {
		t.Fatal("expected GetBalance to succeed")
	}
	if bal != 8_000_000 {
		t.Errorf("balance = %d, want 8000000 (traffic value preserved)", bal)
	}
}

func TestCreditStore_NilClient_SeedBalance(t *testing.T) {
	cs := NewCreditStore(nil, "test:credits:")
	ok := cs.SeedBalance("key1", 100)
	if ok {
		t.Fatal("SeedBalance should return false with nil client")
	}
}

func TestCreditStore_Redis_NonexistentKey(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	cs := NewCreditStore(client, "test:credits:nokey:"+ts+":")

	bal, ok := cs.GetBalance("nonexistent-" + ts)
	if !ok {
		t.Fatal("expected ok=true for nonexistent key")
	}
	if bal != 0 {
		t.Errorf("balance = %d, want 0 for nonexistent key", bal)
	}
}
