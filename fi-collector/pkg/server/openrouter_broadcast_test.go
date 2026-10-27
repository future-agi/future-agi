package server

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
)

// The OpenRouter Broadcast fixture shared with the converter tests.
const openRouterBroadcastFixture = "../../exporter/clickhouse25exporter/testdata/openrouter_broadcast_otlp.json"

// TestOTLPHTTPJSONOpenRouterBroadcast posts OpenRouter Broadcast's OTLP/HTTP
// JSON body unchanged and checks the `spans` rows ClickHouse receives: the
// generation span's input/output come from span.input / span.output, its cost
// is OpenRouter's gen_ai.usage.total_cost even with the production pricer
// wired in, and the root and attempt spans stay empty and cost nothing.
func TestOTLPHTTPJSONOpenRouterBroadcast(t *testing.T) {
	body, err := os.ReadFile(openRouterBroadcastFixture)
	if err != nil {
		t.Fatal(err)
	}

	var mu sync.Mutex
	rows := map[string]map[string]any{}
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		if insertTable(r) == "spans" {
			sc := bufio.NewScanner(bytes.NewReader(b))
			sc.Buffer(make([]byte, 0, 1<<16), 1<<22)
			mu.Lock()
			for sc.Scan() {
				var row map[string]any
				if json.Unmarshal(sc.Bytes(), &row) == nil {
					if id, ok := row["id"].(string); ok {
						rows[id] = row
					}
				}
			}
			mu.Unlock()
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer chSrv.Close()

	writer, err := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		InitialBackoff: time.Millisecond,
		MaxBackoff:     time.Millisecond,
		RequestTimeout: 2 * time.Second,
		DeadLetterFile: t.TempDir() + "/dl.jsonl",
	})
	if err != nil {
		t.Fatal(err)
	}
	priceTable, err := pricing.LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	httpAddr := testTCPAddr(t)
	s := New(Config{
		GRPCAddr:     testTCPAddr(t),
		HTTPAddr:     httpAddr,
		BatchMaxRows: 10,
		BatchMaxAge:  50 * time.Millisecond,
	}, writer, nil, nil, nil, WithPricer(pricing.New(priceTable, nil)))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if conn, err := net.DialTimeout("tcp", httpAddr, 50*time.Millisecond); err == nil {
			conn.Close()
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	resp, err := http.Post("http://"+httpAddr+"/v1/traces", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d, want 200", resp.StatusCode)
	}

	deadline = time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		n := len(rows)
		mu.Unlock()
		if n >= 4 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(rows) != 4 {
		t.Fatalf("spans rows written = %d, want 4", len(rows))
	}

	gen := rows["53995c3f42cd8ad8"]
	for k, want := range map[string]any{
		"input":            `[{"role":"user","content":"Hello!"}]`,
		"output":           `{"role":"assistant","content":"Hi! How can I help you today?"}`,
		"observation_type": "llm",
		"model":            "openai/gpt-4o",
		"service_name":     "openrouter",
		"parent_span_id":   "00f067aa0ba902b7",
		"cost":             0.0001275, // gen_ai.usage.total_cost
	} {
		if gen[k] != want {
			t.Errorf("generation %s = %#v, want %#v", k, gen[k], want)
		}
	}
	for _, k := range []string{"prompt_tokens", "completion_tokens", "total_tokens"} {
		if gen[k] == float64(0) || gen[k] == nil {
			t.Errorf("generation %s = %v, want the gen_ai.usage value", k, gen[k])
		}
	}
	for _, id := range []string{"00f067aa0ba902b7", "1a2b3c4d5e6f7081", "2b3c4d5e6f708192"} {
		if r := rows[id]; r["input"] != "" || r["output"] != "" {
			t.Errorf("span %s input/output = %q/%q, want empty", id, r["input"], r["output"])
		}
		if r := rows[id]; r["cost"] != float64(0) {
			t.Errorf("span %s cost = %v, want 0 (only the generation span is charged)", id, r["cost"])
		}
	}
}
