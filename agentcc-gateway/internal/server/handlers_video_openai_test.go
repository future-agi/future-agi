package server

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"mime/multipart"
	"net/http/httptest"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

// openAIForm builds the multipart body openai-python's videos.create sends.
func openAIForm(t *testing.T, fields map[string]string) (string, string) {
	t.Helper()
	var b bytes.Buffer
	w := multipart.NewWriter(&b)
	for _, k := range []string{"prompt", "model", "seconds", "size"} {
		if v, ok := fields[k]; ok {
			_ = w.WriteField(k, v)
		}
	}
	_ = w.Close()
	return b.String(), w.FormDataContentType()
}
func (f *videoHTTPFixture) form(key, idem, body, contentType string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", "/v1/videos", strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer "+key)
	r.Header.Set("Content-Type", contentType)
	if idem != "" {
		r.Header.Set("Idempotency-Key", idem)
	}
	w := httptest.NewRecorder()
	f.s.httpServer.Handler.ServeHTTP(w, r)
	return w
}
func TestVideoOpenAIWireCreateStatusListDelete(t *testing.T) {
	f := newVideoHTTP(t)
	body, ct := openAIForm(t, map[string]string{"model": videoTestModel, "prompt": "synthetic openai-shaped request", "seconds": "5", "size": "1280x720"})
	w := f.form(f.keyA, "openai-1", body, ct)
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	o := jsonObject(t, w)
	if o["object"] != "video" || o["status"] != "queued" || o["gateway_status"] != "submitting" || o["seconds"] != "5" || o["size"] != "1280x720" || o["progress"] != float64(0) || o["error"] != nil || o["completed_at"] != nil {
		t.Fatalf("%v", o)
	}
	if _, leaked := o["prompt"]; leaked {
		t.Fatal("prompt echoed")
	}
	id := o["id"].(string)
	// Replaying the SDK request with the same key returns the same job.
	if again := jsonObject(t, f.form(f.keyA, "openai-1", body, ct)); again["id"] != id {
		t.Fatal(again)
	}
	got := jsonObject(t, f.request("GET", "/v1/videos/"+id, f.keyA, "", ""))
	if got["status"] != "queued" || got["gateway_status"] != "submitting" || got["model"] != videoTestModel {
		t.Fatal(got)
	}
	// Native JSON keeps the gateway status contract and no OpenAI overlay.
	native := jsonObject(t, f.request("POST", "/v1/videos", f.keyA, "native-1", videoTestBody))
	if native["status"] != "submitting" || native["gateway_status"] != nil || native["seconds"] != "5" || native["size"] != "1280x720" {
		t.Fatalf("native contract changed: %v", native)
	}
	page := jsonObject(t, f.request("GET", "/v1/videos?limit=1", f.keyA, "", ""))
	if page["object"] != "list" || page["has_more"] != true || page["first_id"] == nil || page["first_id"] != page["last_id"] {
		t.Fatal(page)
	}
	next := jsonObject(t, f.request("GET", "/v1/videos?limit=1&after="+page["last_id"].(string), f.keyA, "", ""))
	rows := next["data"].([]any)
	if len(rows) != 1 || rows[0].(map[string]any)["id"] == page["first_id"] || next["has_more"] != false {
		t.Fatal(next)
	}
	if w = f.request("GET", "/v1/videos?after=x&offset=1", f.keyA, "", ""); w.Code != 400 {
		t.Fatal("after and offset together accepted", w.Code)
	}
	// Unknown and foreign cursors are refused, not answered with an empty page.
	for _, cursor := range []string{"video_unknown", id} {
		key := f.keyA
		if cursor == id {
			key = f.keyB
		}
		if w = f.request("GET", "/v1/videos?after="+cursor, key, "", ""); w.Code != 400 || !strings.Contains(w.Body.String(), "invalid_filter") {
			t.Fatal("cursor", cursor, w.Code, w.Body.String())
		}
	}
	if other := jsonObject(t, f.request("GET", "/v1/videos", f.keyB, "", "")); len(other["data"].([]any)) != 0 {
		t.Fatal("cross-tenant list", other)
	}
	if w = f.request("GET", "/v1/videos/"+id+"/content?variant=thumbnail", f.keyA, "", ""); w.Code != 400 || !strings.Contains(w.Body.String(), "unsupported_variant") {
		t.Fatal(w.Code, w.Body.String())
	}
	deleted := jsonObject(t, f.request("DELETE", "/v1/videos/"+id, f.keyA, "", ""))
	if deleted["object"] != "video.deleted" || deleted["deleted"] != true || deleted["id"] != id || deleted["deletion_scope"] != "local_only" {
		t.Fatal(deleted)
	}
	if nativeDel := jsonObject(t, f.request("DELETE", "/v1/videos/"+native["id"].(string), f.keyA, "", "")); nativeDel["object"] != "video" {
		t.Fatal(nativeDel)
	}
}

// One Idempotency-Key cannot be replayed across the native and OpenAI shapes:
// the response dialect is fixed at creation, so the second shape conflicts.
func TestVideoIdempotencyKeyIsBoundToWire(t *testing.T) {
	f := newVideoHTTP(t)
	if w := f.request("POST", "/v1/videos", f.keyA, "cross-wire", videoTestBody); w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	body, ct := openAIForm(t, map[string]string{"model": videoTestModel, "prompt": "private prompt"})
	if w := f.form(f.keyA, "cross-wire", body, ct); w.Code != 409 || !strings.Contains(w.Body.String(), "idempotency_conflict") {
		t.Fatal("cross-wire replay", w.Code, w.Body.String())
	}
	if w := f.form(f.keyA, "openai-first", body, ct); w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := f.request("POST", "/v1/videos", f.keyA, "openai-first", videoTestBody); w.Code != 409 {
		t.Fatal("reverse cross-wire replay", w.Code, w.Body.String())
	}
}
func TestVideoOpenAIWireValidation(t *testing.T) {
	f := newVideoHTTP(t)
	for name, tc := range map[string]struct {
		fields map[string]string
		idem   string
		code   string
	}{
		"missing idempotency": {map[string]string{"model": videoTestModel, "prompt": "p", "seconds": "5"}, "", "missing_idempotency_key"},
		"sora size":           {map[string]string{"model": videoTestModel, "prompt": "p", "size": "1792x1024"}, "k1", "unsupported_combination"},
		"sora model":          {map[string]string{"model": "sora-2", "prompt": "p", "seconds": "4"}, "k2", "unsupported_model"},
		"seconds":             {map[string]string{"model": videoTestModel, "prompt": "p", "seconds": "four"}, "k3", "invalid_parameter"},
	} {
		t.Run(name, func(t *testing.T) {
			body, ct := openAIForm(t, tc.fields)
			w := f.form(f.keyA, tc.idem, body, ct)
			if w.Code != 400 || !strings.Contains(w.Body.String(), tc.code) {
				t.Fatal(w.Code, w.Body.String())
			}
		})
	}
	body, ct := openAIForm(t, map[string]string{"model": videoTestModel, "prompt": "p", "seconds": "5"})
	if w := f.form(f.keyB+"x", "k4", body, ct); w.Code != 401 {
		t.Fatal("unauthenticated multipart accepted", w.Code)
	}
	if w := f.form(f.denied, "k5", body, ct); w.Code != 403 {
		t.Fatal("model permission bypassed by OpenAI wire", w.Code, w.Body.String())
	}
	if w := f.request("POST", "/v1/videos", f.keyA, "k6", `{"model":"`+videoTestModel+`","seconds":"5","duration_seconds":5}`); w.Code != 400 {
		t.Fatal("mixed native/OpenAI JSON accepted", w.Code)
	}
}

// TestVideoOpenAISDKContract drives the real openai-python SDK against the
// gateway's actual HTTP handler and lifecycle worker (fake upstream adapter).
// It runs only when VIDEO_OPENAI_SDK_PYTHON names a Python with openai installed.
func TestVideoOpenAISDKContract(t *testing.T) {
	runClientContract(t, "VIDEO_OPENAI_SDK_PYTHON", "testdata/openai_sdk_contract.py", "OPENAI_SDK_CONTRACT_OK")
}

// TestVideoLiteLLMContract drives LiteLLM's video client (custom_llm_provider
// openai, api_base pointed at the gateway). It runs only when
// VIDEO_LITELLM_PYTHON names a Python with litellm installed.
func TestVideoLiteLLMContract(t *testing.T) {
	runClientContract(t, "VIDEO_LITELLM_PYTHON", "testdata/litellm_video_contract.py", "LITELLM_CONTRACT_OK")
}
func runClientContract(t *testing.T, env, script, marker string) {
	py := os.Getenv(env)
	if py == "" {
		t.Skip(env + " unset")
	}
	caps := capability.BytePlus()
	caps.PollMinInterval = time.Second
	done := provider.Observation{Normalized: provider.StateCompleted, ProviderState: "succeeded", Outputs: []provider.OutputRef{{ContentType: "video/mp4"}}, Usage: &provider.Usage{Lines: []provider.UsageLine{{Unit: provider.VideoTokens, Quantity: 1000, Source: provider.UsageProviderReported}}}}
	f := newVideoHTTPScript(t, videotest.Script{Capabilities: caps, Submit: videotest.JSONReply(provider.SubmitResult{ProviderJobID: "fake-id", Normalized: provider.StateQueued}), Poll: []videotest.Reply{videotest.JSONReply(done)}, Fetch: videotest.Reply{Body: videotest.MP4()}}, func(vc *config.VideoConfig) { vc.Limits.OrgSubmitRPM = 1000 })
	srv := httptest.NewServer(f.s.httpServer.Handler)
	t.Cleanup(srv.Close)
	worker := f.service.NewWorker()
	worker.Start()
	t.Cleanup(func() { _ = worker.Stop(context.Background()) })
	sum := sha256.Sum256(videotest.MP4())
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, py, script)
	cmd.Env = append(os.Environ(), "GATEWAY_BASE_URL="+srv.URL+"/v1", "GATEWAY_KEY="+f.keyA, "GATEWAY_OTHER_KEY="+f.keyB, "GATEWAY_MODEL="+videoTestModel, "EXPECTED_SHA256="+hex.EncodeToString(sum[:]))
	out, err := cmd.CombinedOutput()
	t.Logf("%s", out)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(out), marker) {
		t.Fatal("contract script did not finish")
	}
	jobs, _, e := f.store.ListByOrg("a", video.VideoListFilters{Limit: 100})
	if e != nil {
		t.Fatal(e)
	}
	for _, j := range jobs {
		if j.Wire != capability.WireOpenAI {
			t.Fatalf("SDK job %s stored as wire %q", j.ID, j.Wire)
		}
	}
}
