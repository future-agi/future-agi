package auth

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Python's test_collector_header_interop reproduces this same fixture through
// issue_platform_grant -> emit_spans_to_collector -> OTLPSpanExporter. This is
// parser/wire interop, not a claim of running a deployed collector or transport.
func TestProducerNativeHeadersReachCollectorGrantParser(t *testing.T) {
	raw, err := os.ReadFile("testdata/platform_emit_headers.txt")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Pepper       string
		Now          int64
		Organization string
		Workspace    string
		Identifier   string
		Headers      [][2]string
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	md := metadata.MD{}
	req := httptest.NewRequest("POST", "/v1/traces", nil)
	for _, pair := range fixture.Headers {
		md.Append(pair[0], pair[1])
		req.Header.Add(pair[0], pair[1])
	}
	if len(fixture.Headers) != 2 || !HasGRPCCredentials(md) || len(md.Get("x-fa-grant")) != 0 {
		t.Fatal("producer did not provide the exact native gRPC header pair")
	}
	api, token := firstMD(md, "x-api-key"), firstMD(md, "x-secret-key")
	if api != "fit_"+fixture.Identifier || token == "" {
		t.Fatal("native gRPC parser lost the grant public id or token")
	}
	httpAPI, httpToken := extractCredentials(req)
	if httpAPI != api || httpToken != token {
		t.Fatal("HTTP and gRPC native parsers disagree on producer bytes")
	}
	peppers := mustPeppers(t, fixture.Pepper)
	claims, err := VerifyCollectorGrant(api, token, peppers, []string{"k1"}, time.Unix(fixture.Now, 0))
	if err != nil || claims == nil || claims.Kind != "platform-emit" || claims.Org != fixture.Organization || claims.Workspace != fixture.Workspace {
		t.Fatalf("collector rejected producer's signed header bytes: %v", err)
	}
	if _, err := VerifyCollectorGrant(api, token, peppers, []string{"k1"}, time.Unix(fixture.Now+60, 0)); err == nil {
		t.Fatal("native headers bypassed exact grant expiry")
	}
	lone := metadata.Pairs("x-fa-grant", token)
	if HasGRPCCredentials(lone) {
		t.Fatal("lone x-fa-grant is not the collector credential contract")
	}
	a := &Authenticator{}
	ctx := metadata.NewIncomingContext(context.Background(), lone)
	if result, err := a.AuthenticateGRPC(ctx); result != nil || status.Code(err) != codes.Unauthenticated {
		t.Fatalf("gRPC ingress did not refuse lone x-fa-grant: %v", err)
	}
}
