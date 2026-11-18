package server

import (
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestProjectionR3SafeOutputsAndPostStreams(t *testing.T) {
	for _, rt := range guardedRoutes {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", rt.name, stream), func(t *testing.T) {
				srv, calls := projectionR3Server(t, "post", "enforce", "")
				req := rt.request(stream, "safe text")
				req.Header.Set("Authorization", "Bearer sk-agentcc-r2-a")
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				want, n := 200, int32(1)
				if stream {
					want, n = 403, 0
				}
				t.Logf("status=%d calls=%d released_bytes=%d", w.Code, calls.Load(), w.Body.Len())
				if w.Code != want || calls.Load() != n {
					t.Fatalf("want %d/%d got %d/%d: %s", want, n, w.Code, calls.Load(), w.Body.String())
				}
				if stream && strings.Contains(w.Body.String(), "safe reply") {
					t.Fatal("post Block stream released an event")
				}
			})
		}
	}
}

func TestProjectionR3InputOnlyDirectHandlers(t *testing.T) {
	for _, path := range []string{"/v1/embeddings", "/v1/audio/speech", "/v1/audio/speech/stream"} {
		for _, stage := range []string{"pre", "post"} {
			t.Run(path+"/"+stage, func(t *testing.T) {
				check := "pii-detection"
				if stage == "post" {
					check = "r2-post"
				}
				srv, wire, _ := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy("enforce", "block", check, nil))})
				body := `{"model":"tts-1","input":"safe speech","voice":"alloy"}`
				if path == "/v1/embeddings" {
					body = `{"model":"text-embedding-3-small","input":["first safe","second safe"]}`
				}
				r := httptest.NewRequest("POST", path, strings.NewReader(body))
				r.Header.Set("Authorization", "Bearer sk-agentcc-r2-a")
				w := httptest.NewRecorder()
				switch path {
				case "/v1/embeddings":
					srv.handlers.CreateEmbedding(w, r)
				case "/v1/audio/speech":
					srv.handlers.CreateSpeech(w, r)
				default:
					srv.handlers.StreamSpeech(w, r)
				}
				_, bodies := wire.snapshot()
				want, calls := 200, 1
				if stage == "post" {
					want, calls = 403, 0
				}
				t.Logf("status=%d calls=%d released_bytes=%d", w.Code, len(bodies), w.Body.Len())
				if w.Code != want || len(bodies) != calls {
					t.Fatalf("want %d/%d got %d/%d: %s", want, calls, w.Code, len(bodies), w.Body.String())
				}
			})
		}
	}
}

func TestProjectionR3ResponsesPlainDefaults(t *testing.T) {
	for _, input := range []string{`""`, `[{"role":"user","content":""}]`, `"safe text"`} {
		for _, stage := range []string{"pre", "post"} {
			t.Run(stage+"/"+input, func(t *testing.T) {
				reply := `{"id":"r3-defaults","object":"response","model":"gpt-5.5","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"safe reply","annotations":[]}]}],"error":null,"incomplete_details":null,"instructions":null,"previous_response_id":null,"reasoning":{"effort":null,"summary":null},"text":{"format":{"type":"text"}},"tool_choice":"auto","tools":[],"metadata":{},"store":false,"user":"synthetic-user","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}`
				srv, calls := projectionR3Server(t, stage, "enforce", reply)
				w := routeR2Send(srv, "/v1/responses", `{"model":"gpt-5.5","input":`+input+`,"user":"synthetic-user","store":false}`, "a")
				t.Logf("status=%d calls=%d released_bytes=%d", w.Code, calls.Load(), w.Body.Len())
				if w.Code != 200 || calls.Load() != 1 || w.Body.String() != reply {
					t.Fatalf("safe defaults changed: %d/%d %s", w.Code, calls.Load(), w.Body.String())
				}
			})
		}
	}
}

func TestProjectionR3NativeCompletionAllChoices(t *testing.T) {
	for position := -1; position < 3; position++ {
		t.Run(fmt.Sprintf("violation=%d", position), func(t *testing.T) {
			texts := []string{"first safe", "second safe", "third safe"}
			if position >= 0 {
				texts[position] = "runtime-sensitive"
			}
			reply := fmt.Sprintf(`{"id":"all-choices","model":"gpt-3.5-turbo-instruct","choices":[{"index":0,"text":%q},{"index":1,"text":%q},{"index":2,"text":%q}],"usage":{"prompt_tokens":5,"completion_tokens":6,"total_tokens":11}}`, texts[0], texts[1], texts[2])
			srv, calls := projectionR3Server(t, "post", "enforce", reply)
			w := routeR2Send(srv, "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":["first prompt","second prompt"],"n":3}`, "a")
			want := 403
			if position < 0 {
				want = 200
			}
			t.Logf("status=%d calls=%d released_bytes=%d", w.Code, calls.Load(), w.Body.Len())
			if w.Code != want || calls.Load() != 1 {
				t.Fatalf("want %d/1 got %d/%d: %s", want, w.Code, calls.Load(), w.Body.String())
			}
			if position < 0 {
				for _, text := range texts {
					if !strings.Contains(w.Body.String(), text) {
						t.Fatalf("wire choice lost: %s", w.Body.String())
					}
				}
			} else if strings.Contains(w.Body.String(), "runtime-sensitive") {
				t.Fatal("unchecked choice released")
			}
		})
	}
}
