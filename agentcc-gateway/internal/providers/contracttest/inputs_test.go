package contracttest

import (
	"encoding/binary"
	"errors"
	"io/fs"
	"path/filepath"
	"strings"
	"testing"
)

func TestInputFile(t *testing.T) {
	repo := fixtureTree(t, syntheticMeta)
	dir := filepath.Join(repo, "acme-1", "transcribe")
	writeFile(t, filepath.Join(dir, RequestFile), `{"model":"acme-1","file":"audio.wav","language":"en"}`)
	writeFile(t, filepath.Join(dir, "audio.wav"), "RIFF....WAVE")
	writeFile(t, filepath.Join(dir, ResponseJSONFile), `{"text":"hi"}`)
	writeFile(t, filepath.Join(dir, CaseFile), `{"endpoint": "transcription"}`)
	// Replayed only: the upload is named, its bytes are not in the repo.
	dir = filepath.Join(repo, "acme-1", "translate")
	writeFile(t, filepath.Join(dir, RequestFile), `{"model":"acme-1","file":"speech.mp3"}`)
	writeFile(t, filepath.Join(dir, ResponseJSONFile), `{"text":"hi"}`)
	writeFile(t, filepath.Join(dir, CaseFile), `{"endpoint": "translation"}`)

	var cases []Case
	ft := runFake(func(tb *fakeTB) { cases = discover(tb, "acme", repo, "", false) })
	if len(ft.errors) != 0 {
		t.Fatalf("unexpected errors: %q", ft.errors)
	}
	for _, c := range cases {
		in, err := c.InputFile()
		switch c.Name {
		case "transcribe":
			if err != nil || in == nil || in.Name != "audio.wav" || string(in.Data) != "RIFF....WAVE" {
				t.Errorf("InputFile() = %+v, %v", in, err)
			}
		case "translate":
			if in != nil || !errors.Is(err, fs.ErrNotExist) || !strings.Contains(err.Error(), `"speech.mp3"`) {
				t.Errorf("InputFile() without the file = %+v, %v; want an fs.ErrNotExist naming it", in, err)
			}
			if name, err := InputFileName(c.Endpoint, c.RawRequest); name != "speech.mp3" || err != nil {
				t.Errorf("InputFileName = %q, %v", name, err)
			}
		default:
			if err != nil || in != nil {
				t.Errorf("%s: a chat case has no upload, got %+v, %v", c.Name, in, err)
			}
		}
	}

	// Live mode: a recording without its own request.json uses the repo's
	// upload too.
	live := t.TempDir()
	writeFile(t, filepath.Join(live, "acme-1", "transcribe", ResponseJSONFile), `{"text":"live"}`)
	ft = runFake(func(tb *fakeTB) { cases = discover(tb, "acme", live, repo, true) })
	if len(ft.errors) != 0 || len(cases) != 1 {
		t.Fatalf("live: %d cases, errors %q", len(cases), ft.errors)
	}
	if in, err := cases[0].InputFile(); err != nil || in == nil || in.Name != "audio.wav" {
		t.Errorf("live InputFile() = %+v, %v; want the repo's upload beside the repo's request.json", in, err)
	}
}

func TestReadInputFile_Rejects(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "empty.wav"), "")
	tests := []struct{ req, want string }{
		{`{"file":7}`, "must be a file name string"},
		{`{"file":""}`, "plain file name"},
		{`{"file":"../audio.wav"}`, "plain file name"},
		{`{"file":"a\\b.wav"}`, "plain file name"},
		{`{"file":"request.json"}`, "plain file name"},
		{`{"file":"response.json"}`, "plain file name"},
		{`{"file":"empty.wav"}`, "is empty"},
		{`[1]`, "not a JSON object"},
	}
	for _, tc := range tests {
		_, err := ReadInputFile(dir, EndpointTranslation, []byte(tc.req))
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("ReadInputFile(%s) = %v, want an error containing %q", tc.req, err, tc.want)
		}
	}
	if in, err := ReadInputFile(dir, EndpointEmbeddings, []byte(`{"file":"x.wav"}`)); in != nil || err != nil {
		t.Errorf("a non-multipart endpoint has no upload, got %+v, %v", in, err)
	}
	// No "file": the default upload, a valid half-second WAV.
	in, err := ReadInputFile(dir, EndpointTranscription, []byte(`{"model":"m","response_format":"json"}`))
	if err != nil || in == nil || in.Name != "speech.wav" {
		t.Fatalf("default upload = %+v, %v", in, err)
	}
	d := in.Data
	u32 := func(i int) uint32 { return binary.LittleEndian.Uint32(d[i:]) }
	u16 := func(i int) uint16 { return binary.LittleEndian.Uint16(d[i:]) }
	if len(d) != 44+16000 || string(d[:4]) != "RIFF" || u32(4) != uint32(len(d)-8) || string(d[8:16]) != "WAVEfmt " ||
		u32(16) != 16 || u16(20) != 1 || u16(22) != 1 || u32(24) != 16000 || u32(28) != 32000 || u16(32) != 2 || u16(34) != 16 ||
		string(d[36:40]) != "data" || u32(40) != 16000 {
		t.Errorf("default upload is not a 0.5 s 16 kHz mono PCM WAV: % x", d[:44])
	}
}

func TestDecodePassthrough(t *testing.T) {
	r, err := DecodePassthrough([]byte(`{"method":"GET","path":"/v1/vector_stores","query":{"limit":["2"]}}`))
	if err != nil || r.Method != "GET" || r.Query.Get("limit") != "2" || r.Body != nil || r.Stream() {
		t.Errorf("GET = %+v, %v", r, err)
	}
	r, err = DecodePassthrough([]byte(`{"method":"POST","path":"/v1/threads/runs","body":{"assistant_id":"a","stream":true}}`))
	if err != nil || !r.Stream() || !strings.Contains(string(r.Body), "assistant_id") {
		t.Errorf("POST = %+v, %v", r, err)
	}
	for _, tc := range []struct{ req, want string }{
		{`{"method":"PATCH","path":"/v1/a"}`, "want GET, POST or DELETE"},
		{`{"method":"GET","path":"/v2/a"}`, "want an API path"},
		{`{"method":"GET","path":"/v1/a?b=1"}`, "want an API path"},
		{`{"method":"GET","path":"/v1/a","verb":"x"}`, "unknown field"},
		{`{"model":"m","messages":[]}`, "unknown field"},
	} {
		if _, err := DecodePassthrough([]byte(tc.req)); err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("DecodePassthrough(%s) = %v, want an error containing %q", tc.req, err, tc.want)
		}
	}
	if !PassthroughEndpoint(EndpointVectorStores) || PassthroughEndpoint(EndpointResponses) {
		t.Error("PassthroughEndpoint")
	}
}

func TestResponseFileHelpers(t *testing.T) {
	if got := strings.Join(ResponseFiles(), ","); got != "response.json,response.sse,response.eventstream,response.bin,response.txt" {
		t.Errorf("ResponseFiles() = %s", got)
	}
	if DefaultContentType(ResponseBinFile) != "application/octet-stream" || DefaultContentType("golden.json") != "" {
		t.Error("DefaultContentType")
	}
	if !IsStreamFile(ResponseSSEFile) || !IsStreamFile(ResponseEventStreamFile) || IsStreamFile(ResponseTextFile) {
		t.Error("IsStreamFile")
	}
}
