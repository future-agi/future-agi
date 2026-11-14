package openrouter

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
)

const testModel = "bytedance/seedance-1-5-pro"

func job(model string) *video.JobView {
	return &video.JobView{ID: "video_test", OrgID: "org", KeyID: "key", ModelID: model, Service: "openrouter", Region: "global", Request: video.Request{Model: "openrouter/" + model, Prompt: "Synthetic test clip"}}
}
func testAdapter(t *testing.T, handler http.HandlerFunc) *Adapter {
	t.Helper()
	server := httptest.NewTLSServer(handler)
	t.Cleanup(server.Close)
	a, err := New(Config{Provider: config.VideoProviderConfig{APIKey: "test-api-key", BaseURL: server.URL + "/api/v1"}})
	if err != nil {
		t.Fatal(err)
	}
	a.api.Transport = server.Client().Transport
	a.download.Transport = server.Client().Transport
	return a
}
func submitBodyFor(t *testing.T, j *video.JobView) map[string]any {
	t.Helper()
	var got map[string]any
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.URL.Path != "/api/v1/videos" || r.Header.Get("Authorization") != "Bearer test-api-key" || r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("%s %s auth=%q", r.Method, r.URL.Path, r.Header.Get("Authorization"))
		}
		if e := json.NewDecoder(r.Body).Decode(&got); e != nil {
			t.Error(e)
		}
		w.WriteHeader(202)
		_, _ = w.Write([]byte(`{"id":"gen-vid-1700000000-abcdefghij0123456789","polling_url":"/api/v1/videos/gen-vid-1700000000-abcdefghij0123456789","status":"pending"}`))
	})
	c, e := a.Prepare(context.Background(), j)
	if e != nil {
		t.Fatal(e)
	}
	r, e := a.Submit(context.Background(), j, c)
	if e != nil {
		t.Fatal(e)
	}
	if r.ProviderJobID != "gen-vid-1700000000-abcdefghij0123456789" || r.Normalized != video.StateQueued || r.ProviderState != "pending" {
		t.Fatal(r)
	}
	return got
}
func TestSubmit_MapsGatewayContract(t *testing.T) {
	off := false
	j := job(testModel)
	j.Request.DurationSeconds = 4
	j.Request.Resolution = "480p"
	j.Request.AspectRatio = "16:9"
	j.Request.Audio = &off
	j.Request.ProviderOptions = map[string]any{"seed": json.Number("7")}
	png := base64.StdEncoding.EncodeToString(videotest.PNG())
	j.Request.Inputs = []video.Input{{Role: video.FirstFrame, MediaType: "image/png", Source: video.Source{Data: png}}, {Role: video.LastFrame, MediaType: "image/png", Source: video.Source{Data: png}}}
	got := submitBodyFor(t, j)
	if got["model"] != testModel || got["prompt"] != "Synthetic test clip" || got["duration"] != float64(4) || got["resolution"] != "480p" || got["aspect_ratio"] != "16:9" || got["generate_audio"] != false || got["seed"] != float64(7) {
		t.Fatalf("%v", got)
	}
	for _, field := range []string{"duration_seconds", "audio", "inputs", "provider", "size", "seconds", "input_references", "callback_url", "fps"} {
		if _, ok := got[field]; ok {
			t.Fatalf("unexpected upstream field %s: %v", field, got)
		}
	}
	frames := got["frame_images"].([]any)
	if len(frames) != 2 {
		t.Fatal(frames)
	}
	for i, want := range []string{"first_frame", "last_frame"} {
		f := frames[i].(map[string]any)
		if f["type"] != "image_url" || f["frame_type"] != want || f["image_url"].(map[string]any)["url"] != "data:image/png;base64,"+png {
			t.Fatal(f)
		}
	}
}
func TestSubmit_AudioFlagOnlyWhereCatalogDocumentsIt(t *testing.T) {
	for _, model := range []string{"runway/gen-4.5", "x-ai/grok-imagine-video-1.5-lite", "minimax/hailuo-3-max"} {
		j := job(model)
		j.Request.DurationSeconds = 5
		j.Request.Resolution = "720p"
		if model == "minimax/hailuo-3-max" {
			j.Request.Resolution = "480p"
		}
		got := submitBodyFor(t, j)
		if _, ok := got["generate_audio"]; ok {
			t.Fatalf("%s: generate_audio sent: %v", model, got)
		}
	}
	j := job("google/veo-3.1-lite")
	got := submitBodyFor(t, j)
	if got["generate_audio"] != true || got["duration"] != float64(4) || got["resolution"] != "720p" {
		t.Fatalf("veo default contract: %v", got)
	}
}
func TestSubmit_InvalidBeforeNetwork(t *testing.T) {
	a := testAdapter(t, func(http.ResponseWriter, *http.Request) { t.Fatal("invalid request reached network") })
	for name, mutate := range map[string]func(*video.JobView){
		"duration":   func(j *video.JobView) { j.Request.DurationSeconds = 3 },
		"resolution": func(j *video.JobView) { j.Request.Resolution = "4k" },
		"reference": func(j *video.JobView) {
			j.Request.Inputs = []video.Input{{Role: video.ReferenceImage, MediaType: "image/png", Source: video.Source{Data: "aW1n"}}}
		},
		"identity": func(j *video.JobView) { j.Service = "byteplus" },
		"model":    func(j *video.JobView) { j.ModelID = "openai/sora-2"; j.Request.Model = "openrouter/openai/sora-2" },
		"fps":      func(j *video.JobView) { j.Request.FPS = 24 },
	} {
		t.Run(name, func(t *testing.T) {
			j := job(testModel)
			mutate(j)
			if _, e := a.Submit(context.Background(), j, video.Correlation{}); e == nil {
				t.Fatal("accepted")
			}
		})
	}
}
func pollWith(t *testing.T, body string) (video.Observation, error) {
	t.Helper()
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.URL.Path != "/api/v1/videos/gen-vid-1" || r.Header.Get("Authorization") != "Bearer test-api-key" {
			t.Error(r.Method, r.URL.Path)
		}
		_, _ = w.Write([]byte(body))
	})
	return a.Poll(context.Background(), video.ProviderRef{ProviderJobID: "gen-vid-1"})
}
func TestPoll_StatusMappingPreservesReason(t *testing.T) {
	for _, tc := range []struct {
		status string
		want   video.State
		code   string
	}{{"pending", video.StateQueued, ""}, {"in_progress", video.StateRunning, ""}, {"completed", video.StateCompleted, ""}, {"failed", video.StateFailed, "provider_failed"}, {"expired", video.StateFailed, "provider_expired"}, {"cancelled", video.StateCancelled, ""}} {
		t.Run(tc.status, func(t *testing.T) {
			obs, e := pollWith(t, `{"id":"gen-vid-1","polling_url":"/api/v1/videos/gen-vid-1","status":"`+tc.status+`","error":"upstream said https://secret.example/x?sig=1"}`)
			if e != nil {
				t.Fatal(e)
			}
			if obs.Normalized != tc.want || obs.ProviderState != tc.status {
				t.Fatal(obs)
			}
			if tc.code == "" && obs.Error != nil || tc.code != "" && (obs.Error == nil || obs.Error.Code != tc.code || strings.Contains(obs.Error.Message, "secret")) {
				t.Fatalf("%+v", obs.Error)
			}
		})
	}
	for _, body := range []string{`{"id":"gen-vid-1","status":"queued"}`, `{"id":"gen-vid-other","status":"pending"}`, `{`} {
		var se *video.SchemaError
		if _, e := pollWith(t, body); !errors.As(e, &se) {
			t.Fatalf("%s: %T %v", body, e, e)
		}
	}
}
func TestPoll_UsageCostIsUSDAndNullIsUnknown(t *testing.T) {
	obs, e := pollWith(t, `{"id":"gen-vid-1","status":"completed","unsigned_urls":["https://openrouter.ai/api/v1/videos/gen-vid-1/content?index=0"],"urls":["https://storage.example/signed?token=bearer"],"usage":{"cost":0.0461,"is_byok":false}}`)
	if e != nil {
		t.Fatal(e)
	}
	if obs.Usage == nil || len(obs.Usage.Lines) != 1 || obs.Usage.Lines[0].Unit != video.USD || obs.Usage.Lines[0].Quantity != 0.0461 || obs.Usage.Lines[0].Source != video.UsageProviderReported {
		t.Fatalf("%+v", obs.Usage)
	}
	if len(obs.Outputs) != 1 || obs.Outputs[0].URL != "" {
		t.Fatalf("provider URLs must not be retained: %+v", obs.Outputs)
	}
	for _, body := range []string{
		`{"id":"gen-vid-1","status":"completed","usage":{"cost":null}}`,
		`{"id":"gen-vid-1","status":"completed","usage":{}}`,
		`{"id":"gen-vid-1","status":"completed"}`,
		`{"id":"gen-vid-1","status":"completed","usage":{"cost":0.05,"is_byok":true}}`,
		`{"id":"gen-vid-1","status":"failed"}`,
	} {
		obs, e := pollWith(t, body)
		if e != nil || obs.Usage != nil {
			t.Fatalf("%s: unknown cost must stay unknown: %+v %v", body, obs.Usage, e)
		}
	}
	var se *video.SchemaError
	if _, e := pollWith(t, `{"id":"gen-vid-1","status":"completed","usage":{"cost":-1}}`); !errors.As(e, &se) {
		t.Fatal(e)
	}
}
func TestHTTP_ErrorClassification(t *testing.T) {
	for _, tc := range []struct {
		status    int
		code      string
		retryable bool
	}{{400, "submit_rejected", false}, {401, "provider_access_denied", false}, {402, "provider_insufficient_credits", false}, {403, "provider_access_denied", false}, {408, "provider_unavailable", true}, {429, "provider_rate_limited", true}, {499, "provider_unavailable", true}, {502, "provider_unavailable", true}, {503, "provider_unavailable", true}} {
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Retry-After", "7")
			w.WriteHeader(tc.status)
			_, _ = w.Write([]byte(`{"error":{"code":400,"message":"sk-or-secret-in-body"}}`))
		})
		_, e := a.Submit(context.Background(), job(testModel), video.Correlation{})
		var up *video.UpstreamError
		if !errors.As(e, &up) || up.Status != tc.status || up.Code != tc.code || up.Retryable != tc.retryable || up.RetryAfter != 7*time.Second || strings.Contains(up.Error(), "secret") {
			t.Fatalf("%d: %#v", tc.status, e)
		}
	}
}
func TestFetch_AuthenticatedContentNeverForwardsKey(t *testing.T) {
	mp4 := videotest.MP4()
	var storageAuth string
	storage := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		storageAuth = r.Header.Get("Authorization")
		_, _ = w.Write(mp4)
	}))
	t.Cleanup(storage.Close)
	redirect := false
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/videos/gen-vid-1/content" || r.URL.Query().Get("index") != "0" || r.Header.Get("Authorization") != "Bearer test-api-key" {
			t.Error(r.URL.String(), r.Header.Get("Authorization"))
		}
		if redirect {
			http.Redirect(w, r, storage.URL+"/object.mp4", http.StatusFound)
			return
		}
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write(mp4)
	})
	// Provider-supplied URLs are ignored even when present on the reference.
	ref := video.ProviderRef{ProviderJobID: "gen-vid-1", Outputs: []video.OutputRef{{URL: "https://attacker.example/steal"}}}
	body, meta, e := a.Fetch(context.Background(), ref, 0)
	if e != nil {
		t.Fatal(e)
	}
	got, _ := io.ReadAll(body)
	body.Close()
	if !bytes.Equal(got, mp4) || meta.ContentType != "video/mp4" {
		t.Fatal(len(got), meta)
	}
	redirect = true
	body, _, e = a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "gen-vid-1"}, 0)
	if e != nil {
		t.Fatal(e)
	}
	got, _ = io.ReadAll(body)
	body.Close()
	if !bytes.Equal(got, mp4) || storageAuth != "" {
		t.Fatalf("redirect leaked credentials: %q", storageAuth)
	}
	if _, _, e = a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "../escape"}, 0); e == nil {
		t.Fatal("unsafe provider job id accepted")
	}
	if _, _, e = a.Fetch(context.Background(), ref, 1); e == nil {
		t.Fatal("index beyond observed outputs accepted")
	}
}
func TestFetch_ByteCap(t *testing.T) {
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(bytes.Repeat([]byte{1}, 64)) })
	a.maxDownloadBytes = 16
	body, _, e := a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "gen-vid-1"}, 0)
	if e == nil {
		_, e = io.ReadAll(body)
		body.Close()
	}
	if e == nil {
		t.Fatal("byte cap not enforced")
	}
	a = testAdapter(t, func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(410) })
	var up *video.UpstreamError
	if _, _, e = a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "gen-vid-1"}, 0); !errors.As(e, &up) || up.Status != 410 {
		t.Fatal(e)
	}
}
func TestNew_ConfigValidation(t *testing.T) {
	for name, c := range map[string]config.VideoProviderConfig{
		"key":    {BaseURL: DefaultBaseURL},
		"http":   {APIKey: "k", BaseURL: "http://openrouter.ai/api/v1"},
		"query":  {APIKey: "k", BaseURL: "https://openrouter.ai/api/v1?x=1"},
		"region": {APIKey: "k", Region: "us-east-1"},
		"model":  {APIKey: "k", Models: []string{"openai/sora-2"}},
		"hmac":   {APIKey: "k", SafetyIdentifierMode: "org_key_hmac"},
	} {
		if _, e := New(Config{Provider: c}); e == nil {
			t.Fatal(name)
		}
	}
	a, e := New(Config{Provider: config.VideoProviderConfig{APIKey: "k"}})
	if e != nil || a.base.String() != DefaultBaseURL || a.Capabilities().Route != video.RouteAggregator || a.Capabilities().CancelSupport != video.CancelNone {
		t.Fatal(e)
	}
}
func TestPrepare_UserIdentifierIsKeyedHMAC(t *testing.T) {
	a, e := New(Config{Provider: config.VideoProviderConfig{APIKey: "k", SafetyIdentifierMode: "org_key_hmac"}, CorrelationSecret: "secret"})
	if e != nil {
		t.Fatal(e)
	}
	j := job(testModel)
	c1, _ := a.Prepare(context.Background(), j)
	j.OrgID = "other"
	c2, _ := a.Prepare(context.Background(), j)
	if c1.Token == "" || c1.Token == c2.Token || strings.Contains(c1.Token, "org") || c1.Data["user"] != c1.Token {
		t.Fatal(c1, c2)
	}
	plain, _ := New(Config{Provider: config.VideoProviderConfig{APIKey: "k"}})
	if c, _ := plain.Prepare(context.Background(), job(testModel)); c.Token != "" {
		t.Fatal("identifier sent without org_key_hmac")
	}
}
func TestSchemaPollFixture(t *testing.T) {
	raw, e := os.ReadFile("testdata/poll-completed-schema.json")
	if e != nil {
		t.Fatal(e)
	}
	var j jobResponse
	if e = json.Unmarshal(raw, &j); e != nil {
		t.Fatal(e)
	}
	obs, e := j.observation(j.ID)
	if e != nil || obs.Normalized != video.StateCompleted || obs.Usage == nil || len(obs.Outputs) != 1 {
		t.Fatal(obs, e)
	}
}
func TestContractSuite(t *testing.T) {
	polls := 0
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == "POST":
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"gen-vid-1","polling_url":"/api/v1/videos/gen-vid-1","status":"pending"}`))
		case strings.HasSuffix(r.URL.Path, "/content"):
			_, _ = w.Write(videotest.MP4())
		default:
			polls++
			status := "in_progress"
			if polls > 1 {
				status = "completed"
			}
			_, _ = w.Write([]byte(`{"id":"gen-vid-1","status":"` + status + `","usage":{"cost":0.02}}`))
		}
	})
	videotest.RunContractSuite(t, a, videotest.Fixtures{Job: job(testModel), PollStates: []video.State{video.StateRunning, video.StateCompleted}, FetchBytes: videotest.MP4()})
}
