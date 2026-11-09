package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// IC-R1-8 (R1 preamble P2; X6-R1 ORCHESTRATOR-RULINGS 2026-10-19 03:50 item
// 5 (d), M15 IR-4 both halves): _meta["io.futureagi/tool_call"].executed is
// cc.Executed || cc.Dispatched, "may have executed", and M13's client sets
// Dispatched only once the request's bytes were written to the upstream
// (net/http's WroteRequest for HTTP, the stdin write returning for stdio). A
// call that never left the gateway reports executed=false and its record
// dispatched=false, so it is never metered as executed.

// flipDialer dials normally until fail is set, then refuses every dial.
type flipDialer struct {
	fail atomic.Bool
	d    net.Dialer
}

func (f *flipDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	if f.fail.Load() {
		return nil, errors.New("test dialer: dial refused")
	}
	return f.d.DialContext(ctx, network, address)
}

// addDispatchUpstream registers server id on h: a fresh client, dialling
// through dialer (nil: a plain net.Dialer), on its own meter upstream with
// keep-alives off, so every call dials a new connection.
func addDispatchUpstream(t *testing.T, h *meterHarness, id string, dialer Dialer) *httptest.Server {
	t.Helper()
	up := startMeterUpstream(t)
	up.Config.SetKeepAlivesEnabled(false)
	c := NewClient(ClientConfig{ServerID: id, URL: up.URL, TransportType: "http", Dialer: dialer})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		t.Fatalf("connect %s: %v", id, err)
	}
	t.Cleanup(func() { _ = c.Close() })
	h.s.RegisterClient(c)
	return up
}

// dispatchOutcome is one call's answer and record, as these tests read them.
type dispatchOutcome struct {
	inBand      bool   // answered in band (a result, not a JSON-RPC error)
	executed    any    // _meta["io.futureagi/tool_call"].executed
	code        string // _meta["io.futureagi/error"].code; "" for a result
	message     string
	attribution string
	dispatched  bool // the call's mcp-tool-call-v1 record
}

func callDispatch(t *testing.T, h *meterHarness, tool string) dispatchOutcome {
	t.Helper()
	h.sink.reset()
	resp := h.call(t, tool, nil)
	out := dispatchOutcome{inBand: resp.Error == nil}
	if out.inBand {
		if m := toolCallMetaOf(t, resp); m != nil {
			out.executed = m["executed"]
		}
		var res struct {
			Meta map[string]json.RawMessage `json:"_meta"`
		}
		if err := json.Unmarshal(resp.Result, &res); err != nil {
			t.Fatalf("%s: result %s: %v", tool, resp.Result, err)
		}
		if raw, ok := res.Meta[MetaKeyError]; ok {
			var e struct {
				Code     string         `json:"code"`
				Message  string         `json:"message"`
				Metadata map[string]any `json:"metadata"`
			}
			if err := json.Unmarshal(raw, &e); err != nil {
				t.Fatalf("%s: error object %s: %v", tool, raw, err)
			}
			out.code, out.message = e.Code, e.Message
			out.attribution, _ = e.Metadata["attribution"].(string)
		}
	}
	out.dispatched = h.sink.only(t).Dispatched
	return out
}

// requireNetworkFailure: the call reached callUpstream's CallToolRaw (the
// message is upstreamFailure's "tool call failed: ...", not the unavailable
// server refused before any dispatch) and failed on the network with cause.
func requireNetworkFailure(t *testing.T, got dispatchOutcome, cause string) {
	t.Helper()
	if !got.inBand || got.code != "upstream_error" || got.attribution != "network" ||
		!strings.HasPrefix(got.message, "tool call failed: ") || !strings.Contains(got.message, cause) {
		t.Fatalf("answer %+v, want an in-band upstream_error attributed to the network by CallToolRaw (tool call failed: ... %s)", got, cause)
	}
}

// TestExecutedFalseWhenTheCallNeverLeft: a refused connection (the upstream's
// listener is closed) and a failed dial fail before any request byte is
// written: executed=false, and the record is not dispatched. The same client
// reported its earlier call executed.
func TestExecutedFalseWhenTheCallNeverLeft(t *testing.T) {
	t.Run("refused connection", func(t *testing.T) {
		h := newMeterHarness(t)
		h.onHook(attribute("full"))
		up := addDispatchUpstream(t, h, "gone", nil)
		if got := callDispatch(t, h, "gone_ok"); got.executed != true || !got.dispatched || got.code != "" {
			t.Fatalf("before the upstream went away: %+v, want an executed call", got)
		}
		up.Close()
		got := callDispatch(t, h, "gone_ok")
		requireNetworkFailure(t, got, "connection refused")
		if got.executed != false || got.dispatched {
			t.Errorf("refused connection: executed=%v dispatched=%v, want false and false", got.executed, got.dispatched)
		}
	})
	t.Run("failed dial", func(t *testing.T) {
		h := newMeterHarness(t)
		h.onHook(attribute("full"))
		d := &flipDialer{}
		addDispatchUpstream(t, h, "nodial", d)
		if got := callDispatch(t, h, "nodial_ok"); got.executed != true || !got.dispatched || got.code != "" {
			t.Fatalf("before the dialer refused: %+v, want an executed call", got)
		}
		d.fail.Store(true)
		got := callDispatch(t, h, "nodial_ok")
		requireNetworkFailure(t, got, "test dialer: dial refused")
		if got.executed != false || got.dispatched {
			t.Errorf("failed dial: executed=%v dispatched=%v, want false and false", got.executed, got.dispatched)
		}
	})
}

// TestExecutedTrueOnceTheRequestWasWritten: a call whose request reached the
// upstream may have run there, whatever came back: an answered call, an
// HTTP 500 and a call that timed out after its request was written all
// report executed=true and are recorded dispatched.
func TestExecutedTrueOnceTheRequestWasWritten(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	addDispatchUpstream(t, h, "wrote", nil)
	for _, tc := range []struct{ tool, code string }{
		{"wrote_ok", ""},
		{"wrote_broken", "upstream_error"},
		{"wrote_slow", "upstream_error"},
	} {
		got := callDispatch(t, h, tc.tool)
		if !got.dispatched {
			t.Errorf("%s: the record is not dispatched (%+v)", tc.tool, got)
		}
		if got.inBand && (got.executed != true || got.code != tc.code) {
			t.Errorf("%s: %+v, want executed=true and code %q", tc.tool, got, tc.code)
		}
		if !got.inBand && tc.tool != "wrote_slow" {
			t.Errorf("%s: answered with a JSON-RPC error, want an in-band answer", tc.tool)
		}
	}
}
