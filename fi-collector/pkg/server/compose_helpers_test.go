package server

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// A conversion-only spy, with no provider, configuration or network lookup.
// Every synthetic span lacks a user cost, so the positive control reaches it.
type composePricer struct{ calls atomic.Int32 }

func (p *composePricer) TokenCost(context.Context, string, string, int32, int32) (float64, bool) {
	p.calls.Add(1)
	return 0, true
}
func (p *composePricer) OpenRouterTokenCost(ctx context.Context, org, model string, in, out int32) (float64, bool) {
	return p.TokenCost(ctx, org, model, in, out)
}

type composeTransportOutcome struct {
	code       codes.Code
	httpStatus int
	response   ptraceotlp.ExportResponse
}

func composeSend(t *testing.T, s *Server, conn *grpc.ClientConn, ctx context.Context, transport, api, secret string, req ptraceotlp.ExportRequest, headers http.Header) composeTransportOutcome {
	t.Helper()
	out := composeTransportOutcome{response: ptraceotlp.NewExportResponse()}
	body, err := req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	if transport == "grpc" {
		md := metadata.Pairs("x-api-key", api, "x-secret-key", secret)
		for k, v := range headers {
			md.Set(strings.ToLower(k), v...)
		}
		var response []byte
		err = conn.Invoke(metadata.NewOutgoingContext(ctx, md), exportMethod, &body, &response, grpc.ForceCodec(rawProtoCodec{}))
		out.code = status.Code(err)
		if err == nil {
			if err := out.response.UnmarshalProto(response); err != nil {
				t.Fatal(err)
			}
		}
		return out
	}
	ct := "application/x-protobuf"
	if transport == "http-json" {
		ct = "application/json"
		body, err = req.MarshalJSON()
		if err != nil {
			t.Fatal(err)
		}
	}
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body)).WithContext(ctx)
	r.Header = headers.Clone()
	if r.Header == nil {
		r.Header = make(http.Header)
	}
	r.Header.Set("Content-Type", ct)
	r.Header.Set("X-Api-Key", api)
	r.Header.Set("X-Secret-Key", secret)
	rec := httptest.NewRecorder()
	s.countAuthRefusals(s.auth.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces))).ServeHTTP(rec, r)
	out.httpStatus = rec.Code
	switch rec.Code {
	case http.StatusOK:
		out.code = codes.OK
		if ct == "application/json" {
			err = out.response.UnmarshalJSON(rec.Body.Bytes())
		} else {
			err = out.response.UnmarshalProto(rec.Body.Bytes())
		}
		if err != nil {
			t.Fatal(err)
		}
	case http.StatusForbidden:
		out.code = codes.PermissionDenied
	case http.StatusUnauthorized:
		out.code = codes.Unauthenticated
	case http.StatusTooManyRequests:
		out.code = codes.ResourceExhausted
	case http.StatusServiceUnavailable:
		out.code = codes.Unavailable
	default:
		t.Fatalf("unexpected HTTP status %d: %s", rec.Code, rec.Body.String())
	}
	return out
}

// Synthetic signer uses the explicit RFC 5869 single block rather than the
// production verifier's implementation. Token and pepper never leave loopback.
func composeSignedGrant(t *testing.T, kind string, exp int64) (string, string, *auth.GrantClaims, auth.Config) {
	t.Helper()
	pepper := strings.Repeat("compose-fixture-", 4)
	peppers, err := auth.ResolveSecretPeppers(pepper, nil, "test", "")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	fields := []string{"fe1", "grant", "k1", "fa", "collector-ingest", "-", "-", strconv.FormatInt(now, 10), strconv.FormatInt(exp, 10), strings.Repeat("b", 32), "sandbox", routeOrg, routeWorkspace, routeAgent, "person", "attempt:" + routeAgent + ":" + routeHiddenID, "traces", "-", "-"}
	if kind == "platform" {
		fields[4], fields[10], fields[13], fields[14], fields[15] = "collector-emit", "platform-emit", "-", "svc", "emit:sim"
	}
	canonical := strings.Join(fields, "\n")
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(pepper))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write([]byte("fa>" + fields[4] + "|k1"))
	expand.Write([]byte{1})
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write([]byte(canonical))
	api := "fit_" + fields[9]
	token := "fe1." + base64.RawURLEncoding.EncodeToString([]byte(canonical)) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	cfg := auth.Config{Peppers: peppers, EnvelopeKids: []string{"k1"}}
	claims, err := auth.VerifyCollectorGrant(api, token, peppers, cfg.EnvelopeKids, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	return api, token, claims, cfg
}

func composeNoAdmission(t *testing.T, s *Server, usage *routeUsage, pricer *composePricer) {
	t.Helper()
	s.pendMu.Lock()
	pending := len(s.pend) + len(s.pendProperty) + len(s.pendRoots)
	leased := s.pendLeased
	s.pendMu.Unlock()
	if pending != 0 || leased != 0 || len(usage.snapshot()) != 0 || pricer.calls.Load() != 0 {
		t.Fatal("refused request converted, queued, billed or retained sidecars")
	}
	if s.wire.inUse.Load() != 0 || s.work.inUse.Load() != 0 {
		t.Fatal("refusal leaked high-water lease")
	}
}
