package contracttest

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// Request inputs request.json cannot hold as the endpoint's own JSON body.
//
// Transcription and translation send a multipart form. Their request.json
// holds the canonical request's JSON fields, plus, optionally,
// "file": "<name>", the upload's file name, whose extension is the audio
// format (upstreams read the format from it): "audio.mp3". A contract test
// may upload placeholder bytes under that name, since the replay server
// never listens to them; recording needs real audio, so the recorder uploads
// the file of that name beside request.json and refuses the case while there
// is none. Without "file" the upload is DefaultUpload, a short silent WAV,
// which needs no file.
//
// Assistants and Vector Stores calls carry an HTTP method, path and query as
// well as a body, so their request.json is a PassthroughRequest.
//
// Both sit beside the request.json in use, so in live mode a recording that
// does not bring its own request.json uses the repo's input file too.

// InputFileKey is the request.json key naming a multipart case's upload, as
// the multipart form's field of the same name carries it.
const InputFileKey = "file"

// multipartEndpoints send their request as a multipart form with a file.
var multipartEndpoints = map[string]bool{EndpointTranscription: true, EndpointTranslation: true}

// MultipartEndpoint reports whether endpoint's request uploads a file.
func MultipartEndpoint(endpoint string) bool { return multipartEndpoints[endpoint] }

// InputFile is a multipart case's upload.
type InputFile struct {
	// Name is the upload's file name, e.g. "audio.mp3".
	Name string
	Data []byte
}

// fixtureNames are the files a case directory holds for the harness and the
// recorder, which cannot double as an upload.
var fixtureNames = map[string]bool{
	RequestFile: true, CaseFile: true, MetaFile: true, GoldenFile: true, UpstreamGoldenFile: true,
	"upstream_patch.json": true,
}

// DefaultUpload is what a multipart case whose request.json names no file
// uploads: half a second of 16 kHz mono 16-bit PCM silence, as "speech.wav".
// That is enough for the shape of a plain answer, but a model may answer
// silence with no segments or words at all, so a case whose answer should
// hold them (verbose_json, timestamps, a translation) names a real speech
// file instead.
func DefaultUpload() *InputFile {
	const rate, samples = 16000, 8000
	var b bytes.Buffer
	le := func(v any) { _ = binary.Write(&b, binary.LittleEndian, v) }
	b.WriteString("RIFF")
	le(uint32(36 + 2*samples))
	b.WriteString("WAVE")
	b.WriteString("fmt ")
	le(uint32(16))       // fmt chunk size
	le(uint16(1))        // PCM
	le(uint16(1))        // mono
	le(uint32(rate))     // sample rate
	le(uint32(2 * rate)) // byte rate
	le(uint16(2))        // block align
	le(uint16(16))       // bits per sample
	b.WriteString("data")
	le(uint32(2 * samples))
	b.Write(make([]byte, 2*samples))
	return &InputFile{Name: "speech.wav", Data: b.Bytes()}
}

// InputFileName returns the upload name a multipart endpoint's request.json
// gives under InputFileKey: a plain file name, not a fixture file's. It
// returns "" for other endpoints, and for a request that names none (which
// uploads DefaultUpload).
func InputFileName(endpoint string, rawRequest []byte) (string, error) {
	if !MultipartEndpoint(endpoint) {
		return "", nil
	}
	var head map[string]json.RawMessage
	if err := json.Unmarshal(rawRequest, &head); err != nil {
		return "", fmt.Errorf("%s: not a JSON object: %v", RequestFile, err)
	}
	raw, ok := head[InputFileKey]
	if !ok {
		return "", nil
	}
	var name string
	if err := json.Unmarshal(raw, &name); err != nil {
		return "", fmt.Errorf(`%s: "%s" must be a file name string`, RequestFile, InputFileKey)
	}
	if name == "" || name == "." || name == ".." || strings.ContainsAny(name, `/\`) || fixtureNames[name] || isResponseFile(name) {
		return "", fmt.Errorf(`%s: "%s": %q must be a plain file name other than the fixture files`, RequestFile, InputFileKey, name)
	}
	return name, nil
}

// ReadInputFile returns a multipart endpoint's upload: the file in dir that
// its request.json (rawRequest) names, or DefaultUpload when it names none.
// Other endpoints have none: it returns nil. An error wraps fs.ErrNotExist
// when the name is fine but the file is absent, as it is for a case that is
// only replayed (with placeholder bytes) and never recorded.
func ReadInputFile(dir, endpoint string, rawRequest []byte) (*InputFile, error) {
	if !MultipartEndpoint(endpoint) {
		return nil, nil
	}
	name, err := InputFileName(endpoint, rawRequest)
	if err != nil {
		return nil, err
	}
	if name == "" {
		return DefaultUpload(), nil
	}
	data, err := os.ReadFile(filepath.Join(dir, name))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, fmt.Errorf("%s names the upload %q, but %s has no such file (recording needs real audio there): %w",
			RequestFile, name, dir, fs.ErrNotExist)
	}
	if err != nil {
		return nil, err
	}
	if len(data) == 0 {
		return nil, fmt.Errorf("upload %s is empty", filepath.Join(dir, name))
	}
	return &InputFile{Name: name, Data: data}, nil
}

// InputFile is ReadInputFile for c: the upload beside the request.json in use
// (in live mode, the repo's when the recording did not bring its own). Nil
// for an endpoint that uploads nothing.
func (c Case) InputFile() (*InputFile, error) {
	return ReadInputFile(c.requestDir, c.Endpoint, c.RawRequest)
}

// passthroughEndpoints take a PassthroughRequest.
var passthroughEndpoints = map[string]bool{EndpointAssistants: true, EndpointVectorStores: true}

// PassthroughEndpoint reports whether endpoint's request.json is a
// PassthroughRequest.
func PassthroughEndpoint(endpoint string) bool { return passthroughEndpoints[endpoint] }

// PassthroughRequest is request.json of an assistants or vector_stores case:
// what the gateway's handler forwards to ProxyAssistantsRequest,
// StreamAssistantsRequest or ProxyVectorStoresRequest.
type PassthroughRequest struct {
	// Method is GET, POST or DELETE.
	Method string `json:"method"`
	// Path is the upstream API path, e.g. "/v1/vector_stores/vs_abc/files".
	Path string `json:"path"`
	// Query is the URL query, e.g. {"limit": ["2"]}.
	Query url.Values `json:"query,omitempty"`
	// Body is the JSON body; absent for a GET or DELETE.
	Body json.RawMessage `json:"body,omitempty"`
}

// Stream reports whether the body asks for a stream (an Assistants run).
func (r PassthroughRequest) Stream() bool {
	var head struct {
		Stream bool `json:"stream"`
	}
	_ = json.Unmarshal(r.Body, &head)
	return head.Stream
}

// DecodePassthrough decodes and checks a PassthroughRequest strictly.
func DecodePassthrough(raw []byte) (PassthroughRequest, error) {
	var r PassthroughRequest
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&r); err != nil {
		return r, fmt.Errorf("%s: want {\"method\", \"path\", \"query\", \"body\"}: %v", RequestFile, err)
	}
	switch r.Method {
	case "GET", "POST", "DELETE":
	default:
		return r, fmt.Errorf("%s: method %q: want GET, POST or DELETE", RequestFile, r.Method)
	}
	if !strings.HasPrefix(r.Path, "/v1/") || strings.ContainsAny(r.Path, "?#") || strings.Contains(r.Path, "..") {
		return r, fmt.Errorf("%s: path %q: want an API path such as /v1/vector_stores, without a query", RequestFile, r.Path)
	}
	if len(r.Body) > 0 && !json.Valid(r.Body) {
		return r, fmt.Errorf("%s: body is not JSON", RequestFile)
	}
	return r, nil
}

// ResponseFiles lists the response file names a case may hold (exactly one
// of them), in the order Discover looks for them.
func ResponseFiles() []string {
	names := make([]string, len(responseFiles))
	for i, rf := range responseFiles {
		names[i] = rf.name
	}
	return names
}

func isResponseFile(name string) bool {
	for _, rf := range responseFiles {
		if rf.name == name {
			return true
		}
	}
	return false
}

// DefaultContentType is the Content-Type a response file is replayed with
// when case.json sets none; "" for a name that is not a response file.
func DefaultContentType(file string) string {
	for _, rf := range responseFiles {
		if rf.name == file {
			return rf.contentType
		}
	}
	return ""
}

// IsStreamFile reports whether a response file holds a stream
// (response.sse, response.eventstream).
func IsStreamFile(file string) bool {
	for _, rf := range responseFiles {
		if rf.name == file {
			return rf.stream
		}
	}
	return false
}
