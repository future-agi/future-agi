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

// One Idempotency-Key replayed in the other shape returns the same job (the
// generation request is identical), answered in the requester's dialect.
func TestVideoIdempotentReplayAnswersInRequesterDialect(t *testing.T) {
	f := newVideoHTTP(t)
	native := jsonObject(t, f.request("POST", "/v1/videos", f.keyA, "cross-wire", videoTestBody))
	body, ct := openAIForm(t, map[string]string{"model": videoTestModel, "prompt": "private prompt"})
	replay := jsonObject(t, f.form(f.keyA, "cross-wire", body, ct))
	if replay["id"] != native["id"] || replay["status"] != "queued" || replay["gateway_status"] != "submitting" || native["status"] != "submitting" {
		t.Fatalf("native %v replay %v", native, replay)
	}
	first := jsonObject(t, f.form(f.keyA, "openai-first", body, ct))
	back := jsonObject(t, f.request("POST", "/v1/videos", f.keyA, "openai-first", videoTestBody))
	if back["id"] != first["id"] || back["status"] != "submitting" || back["gateway_status"] != nil {
		t.Fatalf("openai %v native replay %v", first, back)
	}
}

// Every response uses one dialect chosen per request: the explicit header, the
// OpenAI SDKs' X-Stainless-Lang header, else the job's creation shape for a
// single job and native for lists. A list never mixes dialects.
func TestVideoDialectIsChosenPerRequest(t *testing.T) {
	f := newVideoHTTP(t)
	native := jsonObject(t, f.request("POST", "/v1/videos", f.keyA, "d-native", videoTestBody))
	body, ct := openAIForm(t, map[string]string{"model": videoTestModel, "prompt": "openai prompt", "seconds": "5"})
	openai := jsonObject(t, f.form(f.keyA, "d-openai", body, ct))
	get := func(path string, header ...string) map[string]any {
		r := httptest.NewRequest("GET", path, nil)
		r.Header.Set("Authorization", "Bearer "+f.keyA)
		for i := 0; i+1 < len(header); i += 2 {
			r.Header.Set(header[i], header[i+1])
		}
		w := httptest.NewRecorder()
		f.s.httpServer.Handler.ServeHTTP(w, r)
		return jsonObject(t, w)
	}
	statuses := func(list map[string]any) []any {
		out := []any{}
		for _, row := range list["data"].([]any) {
			out = append(out, row.(map[string]any)["status"])
		}
		return out
	}
	if s := statuses(get("/v1/videos")); len(s) != 2 || s[0] != "submitting" || s[1] != "submitting" {
		t.Fatalf("default list must be native: %v", s)
	}
	for _, h := range [][]string{{"X-AgentCC-Video-Dialect", "openai"}, {"X-Stainless-Lang", "python"}} {
		if s := statuses(get("/v1/videos", h...)); len(s) != 2 || s[0] != "queued" || s[1] != "queued" {
			t.Fatalf("%v list must be OpenAI for every row: %v", h, s)
		}
	}
	if o := get("/v1/videos/" + openai["id"].(string)); o["status"] != "queued" || o["gateway_status"] != "submitting" {
		t.Fatalf("creation shape default: %v", o)
	}
	if o := get("/v1/videos/"+openai["id"].(string), "X-AgentCC-Video-Dialect", "native"); o["status"] != "submitting" || o["gateway_status"] != nil {
		t.Fatalf("explicit native: %v", o)
	}
	if o := get("/v1/videos/"+native["id"].(string), "X-Stainless-Lang", "python"); o["status"] != "queued" {
		t.Fatalf("SDK read of a native job: %v", o)
	}
	for _, path := range []string{"/v1/videos/" + openai["id"].(string), "/v1/videos/" + openai["id"].(string) + "/content", "/v1/videos"} {
		r := httptest.NewRequest("GET", path, nil)
		r.Header.Set("Authorization", "Bearer "+f.keyA)
		r.Header.Set("X-AgentCC-Video-Dialect", "sora")
		w := httptest.NewRecorder()
		f.s.httpServer.Handler.ServeHTTP(w, r)
		if w.Code != 400 || !strings.Contains(w.Body.String(), "invalid_video_dialect") {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
	r := httptest.NewRequest("DELETE", "/v1/videos/"+openai["id"].(string), nil)
	r.Header.Set("Authorization", "Bearer "+f.keyA)
	r.Header.Set("X-AgentCC-Video-Dialect", "sora")
	w := httptest.NewRecorder()
	f.s.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != 400 || !strings.Contains(w.Body.String(), "invalid_video_dialect") {
		t.Fatal(w.Code, w.Body.String())
	}
	if still := get("/v1/videos/" + openai["id"].(string)); still["id"] != openai["id"] {
		t.Fatal("rejected header still deleted the job", still)
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
	nativeID := ""
	for _, line := range strings.Split(string(out), "\n") {
		if id, ok := strings.CutPrefix(strings.TrimSpace(line), "NATIVE_JOB "); ok {
			nativeID = id
		}
	}
	jobs, _, e := f.store.ListByOrg("a", video.VideoListFilters{Limit: 100})
	if e != nil {
		t.Fatal(e)
	}
	for _, j := range jobs {
		want := capability.WireOpenAI
		if j.ID == nativeID {
			want = ""
		}
		if j.Wire != want {
			t.Fatalf("job %s stored as wire %q, want %q", j.ID, j.Wire, want)
		}
	}
}
