package server

import (
	"bytes"
	"mime/multipart"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// multipartAudio is a transcription or translation form with a small file.
func multipartAudio(t *testing.T, model string) (string, string) {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	if err := mw.WriteField("model", model); err != nil {
		t.Fatal(err)
	}
	fw, err := mw.CreateFormFile("file", "a.wav")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fw.Write([]byte("RIFF0000WAVE")); err != nil {
		t.Fatal(err)
	}
	if err := mw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.String(), mw.FormDataContentType()
}

// TestPipelineRoutesRecordClientIP: embeddings, images, audio, OCR and rerank
// run the pipeline, so ip_acl judges them too; each must record the TCP peer
// as client_ip, or an enabled list could only refuse them.
func TestPipelineRoutesRecordClientIP(t *testing.T) {
	upstream, _ := accessUpstream(t, `{}`)
	rec := &clientIPRecorder{}
	s := accessServer(t, map[string]config.ProviderConfig{
		"openai": {BaseURL: upstream.URL, APIFormat: "openai",
			Models: []string{"text-embedding-3-small", "dall-e-3", "tts-1", "whisper-1", "ocr-test"}},
		"cohere": {BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "cohere", Models: []string{"rerank-test"}},
	}, accessRules{}, rec)

	transcription, transcriptionType := multipartAudio(t, "whisper-1")
	translation, translationType := multipartAudio(t, "whisper-1")
	reqs := []accessRequest{
		{method: "POST", path: "/v1/embeddings", body: `{"model":"text-embedding-3-small","input":"hi"}`},
		{method: "POST", path: "/v1/images/generations", body: `{"model":"dall-e-3","prompt":"a cat"}`},
		{method: "POST", path: "/v1/audio/speech", body: `{"model":"tts-1","input":"hi","voice":"alloy"}`},
		{method: "POST", path: "/v1/audio/speech/stream", body: `{"model":"tts-1","input":"hi","voice":"alloy"}`},
		{method: "POST", path: "/v1/audio/transcriptions", body: transcription, contentType: transcriptionType},
		{method: "POST", path: "/v1/audio/translations", body: translation, contentType: translationType},
		{method: "POST", path: "/v1/ocr", body: `{"model":"ocr-test","document":{"type":"document_url","document_url":"https://example.com/a.pdf"}}`},
		{method: "POST", path: "/v1/rerank", body: `{"model":"rerank-test","query":"q","documents":["a"]}`},
	}
	for _, req := range reqs {
		rec.take()
		w := req.sendFrom(s, outsideClient)
		got := rec.take()
		if len(got) == 0 {
			t.Errorf("%s: pipeline never ran (%d %s)", req.path, w.Code, w.Body.String())
			continue
		}
		for _, ip := range got {
			if ip != outsideClient {
				t.Errorf("%s: recorded client_ip %q, want TCP peer %q", req.path, ip, outsideClient)
			}
		}
	}
}

// TestEmbeddingsServedUnderGlobalAllowList: with ip_acl enabled, a caller in
// the allow list is served and one outside it is refused.
func TestEmbeddingsServedUnderGlobalAllowList(t *testing.T) {
	upstream, hits := accessUpstream(t, `{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1]}],"model":"text-embedding-3-small","usage":{"prompt_tokens":1,"total_tokens":1}}`)
	s := accessServer(t, map[string]config.ProviderConfig{
		"openai": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"text-embedding-3-small"}},
	}, accessRuleCases["global allow list"])
	checkAccessRules(t, s, hits, []accessRequest{{
		method: "POST", path: "/v1/embeddings", body: `{"model":"text-embedding-3-small","input":"hi"}`,
	}})
}
