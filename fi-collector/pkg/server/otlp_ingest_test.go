package server

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Tests for TH-8743 (gzip on OTLP/HTTP) and TH-8744 (the project
// named by a request header / gRPC metadata when the resource carries no
// project_name). They drive the real listeners with auth disabled, so the
// header's effect is observed where the converter stores it: the row's
// resource_attrs. The resolution of that name through auth (same lookup and
// auto-create as the attribute) is covered in pkg/auth.

func gzipBytes(t *testing.T, raw []byte) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	if _, err := zw.Write(raw); err != nil {
		t.Fatalf("gzip write: %v", err)
	}
	if err := zw.Close(); err != nil {
		t.Fatalf("gzip close: %v", err)
	}
	return buf.Bytes()
}

func postOTLP(t *testing.T, url, contentType string, headers map[string]string, body []byte) (*http.Response, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	req.Header.Set("Content-Type", contentType)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	return resp, string(b)
}

func waitForCH(t *testing.T, sawCH func() (int32, string)) string {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if n, body := sawCH(); n > 0 {
			return body
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("CH not POST'd within 2s")
	return ""
}

// directServer builds a Server whose HTTP handler can be called in-process.
// Only for requests that are rejected before anything is enqueued.
func directServer() *Server {
	return New(Config{BatchMaxRows: 1, BatchMaxAge: time.Second}, nil, nil, nil, nil)
}

func protoBody(t *testing.T, traces ptrace.Traces) []byte {
	t.Helper()
	pb, err := ptraceotlp.NewExportRequestFromTraces(traces).MarshalProto()
	if err != nil {
		t.Fatalf("MarshalProto: %v", err)
	}
	return pb
}

// ── TH-8743: gzip on OTLP/HTTP ─────────────────────────────────────────────

func TestOTLPHTTPGzipProtobuf(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	pb := protoBody(t, makeTraces("gzip-proto-span", "77777777-7777-4777-8777-777777777777"))
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"Content-Encoding": "gzip"}, gzipBytes(t, pb))
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	if got := resp.Header.Get("Content-Type"); got != "application/x-protobuf" {
		t.Errorf("Content-Type = %q, want application/x-protobuf", got)
	}
	if ch := waitForCH(t, sawCH); !strings.Contains(ch, "gzip-proto-span") {
		t.Errorf("CH body missing span name: %q", ch)
	}
}

func TestOTLPHTTPXGzipAlias(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	pb := protoBody(t, makeTraces("x-gzip-span", "7a7a7a7a-7a7a-4a7a-8a7a-7a7a7a7a7a7a"))
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"Content-Encoding": "X-GZip"}, gzipBytes(t, pb))
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	if ch := waitForCH(t, sawCH); !strings.Contains(ch, "x-gzip-span") {
		t.Errorf("CH body missing span name: %q", ch)
	}
}

func TestOTLPHTTPGzipJSONOnLegacyPath(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	js, err := ptraceotlp.NewExportRequestFromTraces(
		makeTraces("gzip-json-span", "88888888-8888-4888-8888-888888888888")).MarshalJSON()
	if err != nil {
		t.Fatalf("MarshalJSON: %v", err)
	}
	// Header values are case-insensitive tokens; the legacy path shares the handler.
	resp, body := postOTLP(t, "http://"+httpAddr+"/tracer/v1/traces", "application/json",
		map[string]string{"Content-Encoding": "GZIP"}, gzipBytes(t, js))
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	if got := resp.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", got)
	}
	if ch := waitForCH(t, sawCH); !strings.Contains(ch, "gzip-json-span") {
		t.Errorf("CH body missing span name: %q", ch)
	}
}

func TestOTLPHTTPIdentityEncodingAccepted(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	pb := protoBody(t, makeTraces("identity-span", "99999999-9999-4999-8999-999999999999"))
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"Content-Encoding": "identity"}, pb)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	waitForCH(t, sawCH)
}

func TestOTLPHTTPUnknownContentEncodingIs415(t *testing.T) {
	for _, enc := range []string{"br", "zstd", "deflate", "gzip, br", "gzip, gzip"} {
		t.Run(enc, func(t *testing.T) {
			pb := protoBody(t, makeTraces("br-span", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
			req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(pb))
			req.Header.Set("Content-Type", "application/x-protobuf")
			req.Header.Set("Content-Encoding", enc)
			rec := httptest.NewRecorder()
			directServer().handleHTTPTraces(rec, req)
			if rec.Code != http.StatusUnsupportedMediaType {
				t.Fatalf("Content-Encoding %q: status %d, want 415 (%s)", enc, rec.Code, rec.Body.String())
			}
			if got := rec.Header().Get("Accept-Encoding"); !strings.Contains(got, "gzip") {
				t.Errorf("Accept-Encoding = %q, want it to list gzip (RFC 7694)", got)
			}
		})
	}
}

func TestOTLPHTTPCorruptGzipIs400(t *testing.T) {
	pb := protoBody(t, makeTraces("corrupt-span", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
	good := gzipBytes(t, pb)
	badCRC := append([]byte(nil), good...)
	badCRC[len(badCRC)-6] ^= 0xff // flip a CRC32 byte in the trailer

	cases := map[string][]byte{
		"not gzip":  pb,
		"truncated": good[:len(good)/2],
		"bad crc":   badCRC,
		"empty":     {},
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/x-protobuf")
			req.Header.Set("Content-Encoding", "gzip")
			rec := httptest.NewRecorder()
			directServer().handleHTTPTraces(rec, req)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status %d, want 400 (%s)", rec.Code, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), "gzip") {
				t.Errorf("400 body should name the gzip failure, got %q", rec.Body.String())
			}
		})
	}
}

// A small gzip body that inflates past the cap is a 413, and the handler
// stops inflating at the cap instead of expanding the whole bomb.
func TestOTLPHTTPGzipBombIs413AndStopsAtTheCap(t *testing.T) {
	// 256 concatenated gzip members of 1 MiB of zeros each: 256 MiB (16x the
	// cap) once inflated, about 256 KiB on the wire. Gzip readers must accept
	// multi-member streams (RFC 1952 section 2.2), so this is a valid body.
	member := gzipBytes(t, make([]byte, 1<<20))
	compressed := bytes.Repeat(member, 256)

	s := directServer()
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(compressed))
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Set("Content-Encoding", "gzip")
	rec := httptest.NewRecorder()
	s.handleHTTPTraces(rec, req)

	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status %d, want 413 (%s)", rec.Code, rec.Body.String())
	}
	// The body is reserved during the count pass; an over-cap bomb releases
	// the reservation before returning.
	if p := s.inflate.peak.Load(); p != maxOTLPHTTPBodyBytes {
		t.Errorf("over-cap body reserved %d bytes, want %d", p, maxOTLPHTTPBodyBytes)
	}
	if u := s.inflate.inUse.Load(); u != 0 {
		t.Errorf("%d budget bytes still leased after the request returned", u)
	}
}

func postGzipDirect(t *testing.T, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Set("Content-Encoding", "gzip")
	rec := httptest.NewRecorder()
	directServer().handleHTTPTraces(rec, req)
	return rec
}

// The cap is inclusive: exactly maxOTLPHTTPBodyBytes inflated bytes pass the
// size check (and then fail decoding, since zeros are not a valid protobuf);
// one more byte is a 413.
func TestOTLPHTTPGzipAtCapIsNot413(t *testing.T) {
	mib := gzipBytes(t, make([]byte, 1<<20))
	var atCap bytes.Buffer
	for i := 0; i < maxOTLPHTTPBodyBytes>>20; i++ {
		atCap.Write(mib)
	}
	if rec := postGzipDirect(t, atCap.Bytes()); rec.Code == http.StatusRequestEntityTooLarge {
		t.Fatalf("a body of exactly %d inflated bytes must not be 413", maxOTLPHTTPBodyBytes)
	}

	atCap.Write(gzipBytes(t, []byte{0}))
	if rec := postGzipDirect(t, atCap.Bytes()); rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("one byte over the cap after inflation: status %d, want 413", rec.Code)
	}
}

// The uncompressed body cap maps to 413.
func TestOTLPHTTPUncompressedOverCapIs413(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(make([]byte, maxOTLPHTTPBodyBytes+1)))
	req.Header.Set("Content-Type", "application/x-protobuf")
	rec := httptest.NewRecorder()
	directServer().handleHTTPTraces(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status %d, want 413 (%s)", rec.Code, rec.Body.String())
	}
}

// ── TH-8744: project from X-Project-Name on OTLP/HTTP ──────────────────────

// This listener has auth disabled, as in the unit test harness: the header
// fills the stored resource attribute but does not resolve or auto-create a
// project identity.
func TestOTLPHTTPProjectHeaderNamesProjectWhenResourceHasNone(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	traces := makeTraces("header-project-span", "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
	traces.ResourceSpans().At(0).Resource().Attributes().Remove("fi.project_id")
	pb := protoBody(t, traces)
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"X-Project-Name": "  header-project  "}, pb)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	ch := waitForCH(t, sawCH)
	if !strings.Contains(ch, `"project_name":"header-project"`) {
		t.Errorf("stored resource_attrs should carry project_name from the header (trimmed): %q", ch)
	}
	if strings.Contains(ch, `"fi.project_id"`) {
		t.Errorf("auth-disabled test listener must not resolve or stamp a project id: %q", ch)
	}
}

func TestOTLPHTTPResourceProjectNameBeatsHeader(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	traces := makeTraces("attr-wins-span", "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
	traces.ResourceSpans().At(0).Resource().Attributes().PutStr("project_name", "attr-project")
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"X-Project-Name": "header-project"}, protoBody(t, traces))
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	ch := waitForCH(t, sawCH)
	if !strings.Contains(ch, `"project_name":"attr-project"`) || strings.Contains(ch, "header-project") {
		t.Errorf("resource project_name must win over the header: %q", ch)
	}
}

func TestOTLPHTTPProjectHeaderWithGzipBody(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	pb := protoBody(t, makeTraces("gzip-header-span", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"))
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"Content-Encoding": "gzip", "x-project-name": "broadcast"}, gzipBytes(t, pb))
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	if ch := waitForCH(t, sawCH); !strings.Contains(ch, `"project_name":"broadcast"`) {
		t.Errorf("stored resource_attrs missing header project: %q", ch)
	}
}

func TestOTLPHTTPInvalidProjectHeaderIs400(t *testing.T) {
	cases := map[string]struct {
		header string
		values []string
	}{
		"conflicting lines":                   {"X-Project-Name", []string{"alpha", "beta"}},
		"repeated lines that agree":           {"X-Project-Name", []string{"alpha", "alpha"}},
		"comma-separated list":                {"X-Project-Name", []string{"alpha, beta"}},
		"comma-joined repeat":                 {"X-Project-Name", []string{"alpha,alpha"}},
		"alias repeated":                      {"FI-Project-Name", []string{"alpha", "alpha"}},
		"invalid utf-8":                       {"X-Project-Name", []string{"bad\xffname"}},
		"longer than 255 characters":          {"X-Project-Name", []string{strings.Repeat("p", 256)}},
		"control character":                   {"X-Project-Name", []string{"a\tb"}},
		"zero width space (Unicode Cf)":       {"X-Project-Name", []string{"check\u200bout"}},
		"bidi override in alias (Unicode Cf)": {"FI-Project-Name", []string{"checkout\u202e"}},
		"only a braille blank (U+2800)":       {"X-Project-Name", []string{"\u2800"}},
		"only a combining mark":               {"X-Project-Name", []string{"\u0301"}},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			pb := protoBody(t, makeTraces("bad-header-span", "ffffffff-ffff-4fff-8fff-ffffffffffff"))
			req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(pb))
			req.Header.Set("Content-Type", "application/x-protobuf")
			for _, v := range tc.values {
				req.Header.Add(tc.header, v)
			}
			rec := httptest.NewRecorder()
			directServer().handleHTTPTraces(rec, req)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status %d, want 400 (%s)", rec.Code, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), tc.header) {
				t.Errorf("400 body should name %s, got %q", tc.header, rec.Body.String())
			}
		})
	}
}

// Both spellings may be sent together when they name the same project.
func TestOTLPHTTPBothProjectHeadersAgreeing(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	pb := protoBody(t, makeTraces("both-headers-span", "19191919-1919-4919-8919-191919191919"))
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"X-Project-Name": "same-project", "FI-Project-Name": " same-project "}, pb)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	if ch := waitForCH(t, sawCH); !strings.Contains(ch, `"project_name":"same-project"`) {
		t.Errorf("stored resource_attrs missing the header project: %q", ch)
	}
}

// traceAI's Java and C# exporters already send FI-Project-Name.
func TestOTLPHTTPFIProjectNameAliasIsRead(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	pb := protoBody(t, makeTraces("alias-header-span", "17171717-1717-4717-8717-171717171717"))
	resp, body := postOTLP(t, "http://"+httpAddr+"/v1/traces", "application/x-protobuf",
		map[string]string{"FI-Project-Name": "alias-project"}, pb)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d (want 200): %s", resp.StatusCode, body)
	}
	if ch := waitForCH(t, sawCH); !strings.Contains(ch, `"project_name":"alias-project"`) {
		t.Errorf("stored resource_attrs missing the FI-Project-Name project: %q", ch)
	}
}

// When every resource names its project the header is not read at all, so a
// malformed one cannot break a request that works today.
func TestOTLPHTTPBadProjectHeaderIgnoredWhenResourceNamesProject(t *testing.T) {
	traces := makeTraces("ignored-header-span", "18181818-1818-4818-8818-181818181818")
	traces.ResourceSpans().At(0).Resource().Attributes().PutStr("project_name", "attr-project")
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(protoBody(t, traces)))
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Add("X-Project-Name", "alpha")
	req.Header.Add("X-Project-Name", "alpha, gamma")
	req.Header.Add("FI-Project-Name", "beta\u200b")
	rec := httptest.NewRecorder()
	New(Config{BatchMaxRows: 1 << 20, BatchMaxAge: time.Hour}, nil, nil, nil, nil).handleHTTPTraces(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d, want 200 (%s)", rec.Code, rec.Body.String())
	}
}

// ── gRPC: x-project-name metadata ──────────────────────────────────────────

// startGRPCOnly boots a gRPC-only server against a stub CH and returns its
// address plus an accessor for the first `spans` insert body.
func startGRPCOnly(t *testing.T, cfg Config) (string, func() (int32, string)) {
	t.Helper()
	var seen atomic.Int32
	var seenBody atomic.Value
	seenBody.Store("")
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		if insertTable(r) == "spans" && seen.Add(1) == 1 {
			seenBody.Store(string(b))
		}
		w.WriteHeader(200)
	}))
	t.Cleanup(chSrv.Close)

	w, err := chwriter.New(chwriter.Config{
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
		t.Fatalf("chwriter.New: %v", err)
	}

	addr := testTCPAddr(t)
	cfg.GRPCAddr = addr
	if cfg.BatchMaxRows == 0 {
		cfg.BatchMaxRows = 1
	}
	if cfg.BatchMaxAge == 0 {
		cfg.BatchMaxAge = 50 * time.Millisecond
	}
	s := New(cfg, w, nil, nil, nil)
	s.cfg.HTTPAddr = "" // gRPC-only; don't race other tests for :4318

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go func() { _ = s.Run(ctx) }()
	if !waitPort(addr, 2*time.Second) {
		t.Fatalf("server didn't listen on %s", addr)
	}
	return addr, func() (int32, string) { return seen.Load(), seenBody.Load().(string) }
}

func grpcClient(t *testing.T, addr string) ptraceotlp.GRPCClient {
	t.Helper()
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return ptraceotlp.NewGRPCClient(conn)
}

func TestGRPCProjectMetadataNamesProjectWhenResourceHasNone(t *testing.T) {
	addr, sawCH := startGRPCOnly(t, Config{})
	client := grpcClient(t, addr)

	ctx := metadata.AppendToOutgoingContext(context.Background(), "x-project-name", "grpc-header-project")
	req := ptraceotlp.NewExportRequestFromTraces(makeTraces("grpc-md-span", "14141414-1414-4414-8414-141414141414"))
	if _, err := client.Export(ctx, req); err != nil {
		t.Fatalf("Export: %v", err)
	}
	if ch := waitForCH(t, sawCH); !strings.Contains(ch, `"project_name":"grpc-header-project"`) {
		t.Errorf("stored resource_attrs should carry project_name from metadata: %q", ch)
	}
}

func TestGRPCResourceProjectNameBeatsMetadata(t *testing.T) {
	addr, sawCH := startGRPCOnly(t, Config{})
	client := grpcClient(t, addr)

	traces := makeTraces("grpc-attr-wins-span", "15151515-1515-4515-8515-151515151515")
	traces.ResourceSpans().At(0).Resource().Attributes().PutStr("project_name", "attr-project")
	ctx := metadata.AppendToOutgoingContext(context.Background(), "x-project-name", "metadata-project")
	if _, err := client.Export(ctx, ptraceotlp.NewExportRequestFromTraces(traces)); err != nil {
		t.Fatalf("Export: %v", err)
	}
	ch := waitForCH(t, sawCH)
	if !strings.Contains(ch, `"project_name":"attr-project"`) || strings.Contains(ch, "metadata-project") {
		t.Errorf("resource project_name must win over metadata: %q", ch)
	}
}

func TestGRPCInvalidProjectMetadataIsInvalidArgument(t *testing.T) {
	addr, _ := startGRPCOnly(t, Config{})
	client := grpcClient(t, addr)

	cases := map[string][]string{
		"conflicting values":   {"x-project-name", "alpha", "x-project-name", "beta"},
		"repeated values":      {"x-project-name", "alpha", "x-project-name", "alpha"},
		"comma-separated list": {"x-project-name", "alpha,beta"},
		"spellings disagree":   {"x-project-name", "alpha", "fi-project-name", "beta"},
		// Format characters (Cf) are not ASCII, which grpc-go refuses to send
		// in a metadata value, so that check is covered in pkg/auth.
	}
	for name, kv := range cases {
		t.Run(name, func(t *testing.T) {
			ctx := metadata.AppendToOutgoingContext(context.Background(), kv...)
			req := ptraceotlp.NewExportRequestFromTraces(makeTraces("grpc-conflict-span", "16161616-1616-4616-8616-161616161616"))
			_, err := client.Export(ctx, req)
			if status.Code(err) != codes.InvalidArgument {
				t.Fatalf("want InvalidArgument, got %v", err)
			}
		})
	}
}
