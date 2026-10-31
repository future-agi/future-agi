package contractwatch

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// AWS's published SigV4 test suite (aws-sig-v4-test-suite): credentials,
// region, service and time shared by every vector.
var (
	vectorCreds = awsCredentials{AccessKeyID: "AKIDEXAMPLE", SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"}
	vectorTime  = time.Date(2015, 8, 30, 12, 36, 0, 0, time.UTC)
)

func TestSignV4Vectors(t *testing.T) {
	tests := []struct {
		name, url, sig string
	}{
		{"get-vanilla", "https://example.amazonaws.com/", "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"},
		{"get-vanilla-query-order-key-case", "https://example.amazonaws.com/?Param2=value2&Param1=value1", "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"},
		{"get-vanilla-empty-query-key", "https://example.amazonaws.com/?Param1=value1", "a67d582fa61cc504c4bae71f336f98b97f1ea3c7a6bfe1b6e45aec72011b9aeb"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, _ := http.NewRequest(http.MethodGet, tc.url, nil)
			signV4(req, vectorCreds, "us-east-1", "service", vectorTime)
			want := "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, SignedHeaders=host;x-amz-date, Signature=" + tc.sig
			if got := req.Header.Get("Authorization"); got != want {
				t.Errorf("Authorization\n got %s\nwant %s", got, want)
			}
			if got := req.Header.Get("X-Amz-Date"); got != "20150830T123600Z" {
				t.Errorf("X-Amz-Date = %s", got)
			}
		})
	}
}

func TestSignV4SessionToken(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "https://bedrock.us-east-1.amazonaws.com/foundation-models", nil)
	creds := vectorCreds
	creds.SessionToken = "token-123"
	signV4(req, creds, "us-east-1", "bedrock", vectorTime)
	if req.Header.Get("X-Amz-Security-Token") != "token-123" ||
		!strings.Contains(req.Header.Get("Authorization"), "SignedHeaders=host;x-amz-date;x-amz-security-token,") {
		t.Errorf("session token not signed: %v", req.Header)
	}
}

const foundationModels = `{"modelSummaries":[
 {"modelArn":"arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-5-5","modelId":"anthropic.claude-opus-5-5","modelName":"Claude Opus 5.5","providerName":"Anthropic",
  "inputModalities":["TEXT","IMAGE"],"outputModalities":["TEXT"],"responseStreamingSupported":true,"customizationsSupported":[],"inferenceTypesSupported":["ON_DEMAND","INFERENCE_PROFILE"],
  "modelLifecycle":{"status":"ACTIVE"}},
 {"modelId":"anthropic.claude-sonnet-5-5","providerName":"Anthropic","outputModalities":["TEXT"],"inferenceTypesSupported":["INFERENCE_PROFILE"],"modelLifecycle":{"status":"ACTIVE"}},
 {"modelId":"amazon.titan-embed-text-v2:0","providerName":"Amazon","outputModalities":["EMBEDDING"],"inferenceTypesSupported":["ON_DEMAND"],"modelLifecycle":{"status":"ACTIVE"}},
 {"modelId":"anthropic.claude-3-haiku-20240307-v1:0","providerName":"Anthropic","outputModalities":["TEXT"],"inferenceTypesSupported":["ON_DEMAND"],"modelLifecycle":{"status":"LEGACY","endOfLifeTime":"2026-12-01T00:00:00Z"}},
 {"modelId":"meta.llama3-70b-instruct-v1:0","providerName":"Meta","outputModalities":["TEXT"],"inferenceTypesSupported":["PROVISIONED"],"modelLifecycle":{"status":"ACTIVE"}}
]}`

func TestBedrockLister(t *testing.T) {
	var mu sync.Mutex
	var seen []string
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r.URL.RequestURI())
		mu.Unlock()
		auth := r.Header.Get("Authorization")
		if !strings.HasPrefix(auth, "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20261028/us-west-2/bedrock/aws4_request, SignedHeaders=host;x-amz-date, Signature=") {
			http.Error(w, `{"message":"bad signature scope: `+auth+`"}`, http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/foundation-models":
			_, _ = w.Write([]byte(foundationModels))
		case r.URL.Path == "/inference-profiles" && r.URL.Query().Get("type") == "SYSTEM_DEFINED" && r.URL.Query().Get("nextToken") == "":
			_, _ = w.Write([]byte(`{"inferenceProfileSummaries":[
				{"inferenceProfileId":"us.anthropic.claude-sonnet-5-5","inferenceProfileName":"US Claude Sonnet 5.5","status":"ACTIVE","type":"SYSTEM_DEFINED","createdAt":"2026-09-01T00:00:00Z",
				 "models":[{"modelArn":"arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-5-5"},{"modelArn":"arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-5-5"}]}],
				"nextToken":"page/2+="}`))
		case r.URL.Path == "/inference-profiles" && r.URL.Query().Get("nextToken") == "page/2+=":
			_, _ = w.Write([]byte(`{"inferenceProfileSummaries":[
				{"inferenceProfileId":"us.anthropic.claude-3-haiku-20240307-v1:0","status":"ACTIVE","type":"SYSTEM_DEFINED",
				 "models":[{"modelArn":"arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"}]}]}`))
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(srv.Close)

	l := BedrockLister{BaseURL: srv.URL, Region: "us-west-2", Creds: vectorCreds, Client: srv.Client(),
		Now: func() time.Time { return time.Date(2026, 10, 28, 9, 0, 0, 0, time.UTC) }}
	ms, err := l.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if ids(ms) != "anthropic.claude-opus-5-5,anthropic.claude-sonnet-5-5,amazon.titan-embed-text-v2:0,anthropic.claude-3-haiku-20240307-v1:0,meta.llama3-70b-instruct-v1:0,us.anthropic.claude-sonnet-5-5,us.anthropic.claude-3-haiku-20240307-v1:0" {
		t.Fatalf("got %s", ids(ms))
	}
	m := byID(ms)
	if o := m["anthropic.claude-opus-5-5"]; o.AutoIgnore != "" || o.OwnedBy != "Anthropic" || !contains(o.Capabilities, "streaming") || !contains(o.Capabilities, "inference:on_demand") {
		t.Errorf("opus: %+v", o)
	}
	for id, want := range map[string]string{
		"anthropic.claude-sonnet-5-5":               "inference profile",
		"amazon.titan-embed-text-v2:0":              "not a text model",
		"anthropic.claude-3-haiku-20240307-v1:0":    "LEGACY",
		"meta.llama3-70b-instruct-v1:0":             "dedicated capacity",
		"us.anthropic.claude-3-haiku-20240307-v1:0": "LEGACY", // inherited from its foundation model
	} {
		if !strings.Contains(m[id].AutoIgnore, want) {
			t.Errorf("%s: AutoIgnore %q, want it to mention %q", id, m[id].AutoIgnore, want)
		}
	}
	p := m["us.anthropic.claude-sonnet-5-5"]
	if p.AutoIgnore != "" || p.OwnedBy != "Anthropic" || p.Created == nil || !contains(p.Capabilities, "profile-of:anthropic.claude-sonnet-5-5") {
		t.Errorf("profile: %+v", p)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(seen) != 3 || !strings.Contains(seen[2], "nextToken=page%2F2%2B%3D") {
		t.Errorf("requests %v (the next token must be query-escaped)", seen)
	}
}

func TestBedrockListerErrorHidesSecret(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"message":"secret was `+vectorCreds.SecretAccessKey+`"}`, http.StatusForbidden)
	}))
	t.Cleanup(srv.Close)
	_, err := BedrockLister{BaseURL: srv.URL, Region: "us-east-1", Creds: vectorCreds, Client: srv.Client()}.List(context.Background())
	if err == nil || strings.Contains(err.Error(), vectorCreds.SecretAccessKey) || !strings.Contains(err.Error(), "HTTP 403") {
		t.Fatalf("got %v", err)
	}
}
