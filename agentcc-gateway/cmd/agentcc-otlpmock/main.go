// agentcc-otlpmock captures OTLP/HTTP JSON and protobuf, never forwarding data.
// Its separate read/control listener must be published on host loopback only.
package main

import (
	"compress/gzip"
	"encoding/json"
	"flag"
	"io"
	"log"
	"net/http"
	"sync"
	"time"

	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

type capture struct {
	mu       sync.Mutex
	batches  []json.RawMessage
	attempts int
	failures int
	status   int
}

func reply(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func (c *capture) ingest(w http.ResponseWriter, r *http.Request) {
	var reader io.Reader = http.MaxBytesReader(w, r.Body, 8<<20)
	if enc := r.Header.Get("Content-Encoding"); enc == "gzip" {
		g, err := gzip.NewReader(reader)
		if err != nil {
			http.Error(w, "bad gzip", 400)
			return
		}
		defer g.Close()
		reader = g
	} else if enc != "" {
		http.Error(w, "unsupported encoding", 415)
		return
	}
	body, err := io.ReadAll(io.LimitReader(reader, (8<<20)+1))
	if err != nil || len(body) > 8<<20 {
		http.Error(w, "body limit", 413)
		return
	}
	// Wire-identical to ExportTraceServiceRequest, without importing gRPC gateway dependencies.
	batch := &tracepb.TracesData{}
	contentType := r.Header.Get("Content-Type")
	switch contentType {
	case "application/json":
		batch, err = decodeOTLPJSON(body)
	case "application/x-protobuf":
		err = proto.Unmarshal(body, batch)
	default:
		http.Error(w, "unsupported content type", 415)
		return
	}
	if err == nil {
		err = validateIDs(batch)
	}
	if err != nil {
		http.Error(w, "bad OTLP", 400)
		return
	}
	// Receipts are OTLP/JSON (hex ids, integer enums) whichever transport the
	// batch came on.
	canonical, err := encodeOTLPJSON(batch)
	if err != nil {
		http.Error(w, "encoding failed", 500)
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.attempts++
	if c.failures > 0 {
		c.failures--
		http.Error(w, "injected failure", c.status)
		return
	}
	if len(c.batches) >= 10000 {
		http.Error(w, "fixture capacity", 507)
		return
	}
	c.batches = append(c.batches, canonical)
	w.Header().Set("Content-Type", contentType)
	if contentType == "application/json" {
		_, _ = w.Write([]byte("{}"))
	} else {
		w.WriteHeader(200)
	}
}

func (c *capture) handlers() (http.Handler, http.Handler) {
	data, control := http.NewServeMux(), http.NewServeMux()
	data.HandleFunc("POST /v1/traces", c.ingest)
	control.HandleFunc("GET /__health", func(w http.ResponseWriter, _ *http.Request) { reply(w, 200, map[string]bool{"ok": true}) })
	control.HandleFunc("GET /__received", func(w http.ResponseWriter, _ *http.Request) {
		c.mu.Lock()
		defer c.mu.Unlock()
		reply(w, 200, map[string]any{"batches": append([]json.RawMessage{}, c.batches...), "attempts": c.attempts})
	})
	control.HandleFunc("POST /__failure", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Count  int `json:"count"`
			Status int `json:"status"`
		}
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body) != nil || body.Count < 0 || body.Count > 1000 ||
			(body.Status != 429 && body.Status != 500 && body.Status != 503) {
			http.Error(w, "invalid failure", 400)
			return
		}
		c.mu.Lock()
		c.failures, c.status = body.Count, body.Status
		c.mu.Unlock()
		reply(w, 200, map[string]bool{"ok": true})
	})
	return data, control
}
func main() {
	addr := flag.String("addr", "127.0.0.1:4318", "data listener")
	controlAddr := flag.String("control", "127.0.0.1:8081", "control listener (publish on loopback only)")
	flag.Parse()
	c := &capture{status: 503}
	data, control := c.handlers()
	go func() {
		log.Fatal((&http.Server{Addr: *controlAddr, Handler: control, ReadHeaderTimeout: 10 * time.Second}).ListenAndServe())
	}()
	log.Fatal((&http.Server{Addr: *addr, Handler: data, ReadHeaderTimeout: 10 * time.Second}).ListenAndServe())
}
