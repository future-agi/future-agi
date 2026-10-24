package server

import (
	"bytes"
	"context"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"
)

// A rule in the config file at the post stage reads the answer, and the
// request with it, where there is a chat answer to withhold. On the APIs
// whose answer is not model text (embeddings, images, speech, rerank, search,
// a transcription's or translation's prompt, embedContent) there is none:
// such a rule gets no request there, so it neither records a block it does
// not enforce nor sends the request's text to an evaluator after the call.
// The checks on the request read those requests before the provider.

// postStageSurfaces are the non-chat routes that run the whole pipeline,
// post stage included. The Assistants API, vector store search and
// /v1/videos run only the request stage (RunRequestPlugins).
func postStageSurfaces() []otherAPISurface {
	var out []otherAPISurface
	for _, sf := range otherAPISurfaces {
		if sf.logged || sf.path == "/v1/search" {
			out = append(out, sf)
		}
	}
	return out
}

// sendCapturingHeaders is send, with the response's headers too.
func (g *otherAPIsGateway) sendCapturingHeaders(t *testing.T, sf otherAPISurface, text string) (int, string, http.Header) {
	t.Helper()
	key := postBlockKey
	if sf.key != "" {
		key = sf.key
	}
	var req *http.Request
	if sf.multipart {
		var buf bytes.Buffer
		mw := multipart.NewWriter(&buf)
		fw, _ := mw.CreateFormFile("file", "speech.mp3")
		_, _ = fw.Write([]byte("ID3-fake-audio"))
		_ = mw.WriteField("model", otherAPIsWhisper)
		_ = mw.WriteField("prompt", sf.body(text))
		_ = mw.Close()
		req = httptest.NewRequest(http.MethodPost, sf.path, &buf)
		req.Header.Set("Content-Type", mw.FormDataContentType())
	} else {
		req = httptest.NewRequest(http.MethodPost, sf.path, bytes.NewReader([]byte(sf.body(text))))
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Authorization", "Bearer "+key)
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b), w.Result().Header
}

func TestAnswerCheck_PostStageRuleRecordsNoBlockOnOtherAPIs(t *testing.T) {
	for _, sf := range postStageSurfaces() {
		t.Run(sf.name, func(t *testing.T) {
			g := newOtherAPIsGateway(t, postBlockSetup{staticRule: requestCheck})
			code, body, header := g.sendCapturingHeaders(t, sf, requestCheckPrompt)
			if code != sf.ok {
				t.Errorf("status %d, want %d: %s", code, sf.ok, body)
			}
			if n := g.providerCalls(); n != 1 {
				t.Errorf("a provider got the request %d times, want 1", n)
			}
			if v := header.Get("X-Agentcc-Guardrail-Triggered"); v != "" {
				t.Errorf("X-Agentcc-Guardrail-Triggered %q on an answer the rule never read", v)
			}
			if !sf.logged {
				return
			}
			e := g.entry(t)
			if e["status_code"] != float64(sf.ok) {
				t.Errorf("log status_code %v, want %d", e["status_code"], sf.ok)
			}
			if e["guardrail_triggered"] == true {
				t.Errorf("the log records guardrail_triggered for a rule that enforced nothing: %v", e)
			}
			if meta, _ := e["metadata"].(map[string]any); meta["guardrail_action"] != nil || meta["guardrail_name"] != nil {
				t.Errorf("the log's metadata records guardrail_action %v, guardrail_name %v", meta["guardrail_action"], meta["guardrail_name"])
			}
		})
	}
}

// POST /v1/threads with an empty body creates an empty thread (OpenAI's own
// example sends none). Under a check that would block the request it goes
// on, as it does with {}: an empty body holds nothing to read.
func TestRequestCheck_EmptyThreadBodyGoesOn(t *testing.T) {
	for _, body := range []string{"", " \n", "{}"} {
		for _, shape := range requestCheckShapes {
			t.Run(shape.name+" / "+strconv.Quote(body), func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				code, resp := g.doAs(t, postBlockKey, "/v1/threads", body, nil)
				if code != http.StatusOK {
					t.Errorf("status %d, want 200: %s", code, resp)
				}
				if n := g.providerCalls(); n != 1 {
					t.Errorf("a provider got the request %d times, want 1", n)
				}
			})
		}
	}
}
