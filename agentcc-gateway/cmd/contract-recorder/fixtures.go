package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// setModel returns the JSON object raw with its top-level "model" set to
// model, keeping the other keys in their order so a cloned request.json
// diffs cleanly against the one it came from. Output is indented two
// spaces, like the committed fixtures.
func setModel(raw []byte, model string) ([]byte, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return nil, fmt.Errorf("request is not a JSON object")
	}
	var compact bytes.Buffer
	compact.WriteByte('{')
	found := false
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil, err
		}
		key := tok.(string)
		var val json.RawMessage
		if err := dec.Decode(&val); err != nil {
			return nil, err
		}
		if key == "model" {
			found = true
			if val, err = marshalNoEscape(model); err != nil {
				return nil, err
			}
		}
		if compact.Len() > 1 {
			compact.WriteByte(',')
		}
		k, _ := marshalNoEscape(key)
		compact.Write(k)
		compact.WriteByte(':')
		compact.Write(val)
	}
	if _, err := dec.Token(); err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, fmt.Errorf("trailing data after the request object")
	}
	if !found {
		return nil, fmt.Errorf(`request has no "model"`)
	}
	compact.WriteByte('}')
	var out bytes.Buffer
	if err := json.Indent(&out, compact.Bytes(), "", "  "); err != nil {
		return nil, err
	}
	out.WriteByte('\n')
	return out.Bytes(), nil
}

func marshalNoEscape(v any) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(b.Bytes(), []byte("\n")), nil
}

// readMeta decodes a meta.json strictly, as contracttest does.
func readMeta(path string) (*contracttest.Meta, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	m := &contracttest.Meta{}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(m); err != nil {
		return nil, fmt.Errorf("%s: %v", path, err)
	}
	return m, nil
}

// encodeMeta writes meta the way the committed files are written: two-space
// indent, no HTML escaping, trailing newline, empty allowlists as [].
func encodeMeta(m *contracttest.Meta) ([]byte, error) {
	if m.AllowedUnknownFields == nil {
		m.AllowedUnknownFields = []contracttest.AllowedField{}
	}
	if m.AllowedUnknownShapes == nil {
		m.AllowedUnknownShapes = []contracttest.AllowedShape{}
	}
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(m); err != nil {
		return nil, err
	}
	return b.Bytes(), nil
}

// markRecorded returns the meta.json at mw.Src updated for a recording made
// on date: source "recorded", recorded_at date. A cloned meta also takes the
// new model ID, keeps the allowlists of the model it was cloned from (so a
// new version starts from the old one's known gaps, and only what changed
// fails), and says where it came from in notes.
func markRecorded(mw metaWrite, date string) ([]byte, error) {
	m, err := readMeta(mw.Src)
	if err != nil {
		return nil, err
	}
	m.Source = contracttest.SourceRecorded
	m.RecordedAt = &date
	if mw.ClonedFrom != "" {
		m.Model = mw.Model
		m.Notes = fmt.Sprintf("Recorded %s by cmd/contract-recorder from the %s cases, cloned with --from %s --as %s. "+
			"The allowlists are %s's: drop entries this model no longer needs (the stale check names them) "+
			"and triage new unknown fields and shapes.", date, mw.ClonedFrom, mw.ClonedFrom, mw.Model, mw.ClonedFrom)
	}
	return encodeMeta(m)
}

// generatorSources are hand-written files a synthetic response is generated
// from, which a recording makes stale. The bedrock suite builds each binary
// response.eventstream from a response.eventstream.json beside it, and its
// -update regenerates the binary from that source, which would overwrite the
// recording; a recorded body has no source, so the source goes.
var generatorSources = []string{contracttest.ResponseEventStreamFile + ".json"}

// writeRecording saves a recorded body for j, plus request.json and any
// upstream_patch.json when the case is cloned. In place, it removes any other
// response file, so the case keeps exactly one, and any generator source. It
// returns the file written and the names of the files it removed.
func writeRecording(j job, body []byte) (string, []string, error) {
	if err := os.MkdirAll(j.OutDir, 0o755); err != nil {
		return "", nil, err
	}
	if j.WriteRequest {
		if err := os.WriteFile(filepath.Join(j.OutDir, contracttest.RequestFile), j.Body, 0o644); err != nil {
			return "", nil, err
		}
		if j.Patch != nil {
			if err := os.WriteFile(filepath.Join(j.OutDir, PatchFile), j.Patch, 0o644); err != nil {
				return "", nil, err
			}
		}
	}
	name := j.ResponseFile()
	var removed []string
	stale := append([]string{contracttest.ResponseJSONFile, contracttest.ResponseSSEFile, contracttest.ResponseEventStreamFile}, generatorSources...)
	for _, other := range stale {
		if other == name {
			continue
		}
		err := os.Remove(filepath.Join(j.OutDir, other))
		switch {
		case err == nil:
			removed = append(removed, other)
		case !errors.Is(err, fs.ErrNotExist):
			return "", removed, err
		}
	}
	dst := filepath.Join(j.OutDir, name)
	return dst, removed, os.WriteFile(dst, body, 0o644)
}

// redact replaces every occurrence of a secret in b. Secrets shorter than 8
// bytes are ignored: they would match ordinary text and are not real keys.
func redact(b []byte, secrets []string) ([]byte, bool) {
	hit := false
	for _, s := range secrets {
		if len(s) < 8 {
			continue
		}
		if bytes.Contains(b, []byte(s)) {
			hit = true
			b = bytes.ReplaceAll(b, []byte(s), []byte("REDACTED"))
		}
	}
	return b, hit
}
