package ext

import (
	"net/http"
	"testing"
)

// credits is /api/v1/credits as {total_credits, total_usage, balance} in micros.
func (m *extMock) credits(t *testing.T) (total, usage, balance int64) {
	t.Helper()
	_, b := m.do(t, "GET", "/api/v1/credits", "")
	d := decode(t, b)["data"].(map[string]any)
	total, usage = usdToMicros(d["total_credits"].(float64)), usdToMicros(d["total_usage"].(float64))
	return total, usage, total - usage
}

// POST /__account sets the balance /api/v1/credits reports.
func TestAccountSetsTheCreditsBalance(t *testing.T) {
	m := newExtMock(t)
	m.chat(t, "key-1", chatBody("test/model-a", false)) // 3,000
	status, b := m.do(t, "POST", "/__account", `{"balance_usd": 12.5}`)
	if status != http.StatusOK || decode(t, b)["balance_usd"] != 12.5 {
		t.Fatalf("status %d body %s", status, b)
	}
	if total, usage, balance := m.credits(t); balance != 12_500_000 || usage != 3000 || total != 12_503_000 {
		t.Fatalf("credits total %d usage %d balance %d; want the balance 12.5 USD with the usage kept", total, usage, balance)
	}
	m.chat(t, "key-1", chatBody("test/model-b", false)) // 1,200
	if _, _, balance := m.credits(t); balance != 12_500_000-1200 {
		t.Fatalf("a call after /__account did not draw the balance down: %d", balance)
	}

	if status, b = m.do(t, "POST", "/__account", `{"balance_usd": "0.25"}`); status != http.StatusOK {
		t.Fatalf("a decimal string: status %d body %s", status, b)
	}
	if _, _, balance := m.credits(t); balance != 250_000 {
		t.Fatalf("balance %d, want 250000", balance)
	}
	for _, bad := range []string{`{}`, `{"balance_usd": -1}`, `{"balance_usd": "lots"}`, `{"balance_usd": null}`, `nope`} {
		if status, b := m.do(t, "POST", "/__account", bad); status != http.StatusBadRequest {
			t.Errorf("%q: status %d body %s", bad, status, b)
		}
	}
	if _, _, balance := m.credits(t); balance != 250_000 {
		t.Fatalf("a refused /__account changed the balance: %d", balance)
	}

	m.do(t, "POST", "/__reset", "")
	if total, usage, _ := m.credits(t); total != 10_000_000 || usage != 0 {
		t.Fatalf("/__reset: total %d usage %d; want the fixtures' 10 USD", total, usage)
	}
}
