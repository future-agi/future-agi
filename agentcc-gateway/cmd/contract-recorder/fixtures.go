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
	"reflect"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// setModel returns the JSON object raw with its top-level "model" set to
// model, keeping the other keys in their order so a cloned request.json
// diffs cleanly against the one it came from. Output is indented two
// spaces, like the committed fixtures. A "models/<id>" value (a GenAI
// embedContent body) keeps its prefix. A request without "model" is an
// error when required, and returned unchanged otherwise (the native GenAI
// bodies, whose model is in the URL).
func setModel(raw []byte, model string, required bool) ([]byte, error) {
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
			var old string
			_ = json.Unmarshal(val, &old)
			id := model
			if strings.HasPrefix(old, "models/") && !strings.HasPrefix(model, "models/") {
				id = "models/" + model
			}
			if val, err = marshalNoEscape(id); err != nil {
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
		if required {
			return nil, fmt.Errorf(`request has no "model"`)
		}
		return raw, nil
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

// encodeIndented writes v the way the committed fixtures are written:
// two-space indent, no HTML escaping, trailing newline.
func encodeIndented(v any) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return b.Bytes(), nil
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
	return encodeIndented(m)
}

// markRecorded returns the meta.json at mw.Src updated for a recording made
// on date: source "recorded", recorded_at date. A cloned meta also takes the
// new model ID and cloned_from, keeps the allowlists of the model it was
// cloned from (so a new version starts from the old one's known gaps, and
// only what changed fails), and says where it came from in notes.
func markRecorded(mw metaWrite, date string) ([]byte, error) {
	m, err := readMeta(mw.Src)
	if err != nil {
		return nil, err
	}
	m.Source = contracttest.SourceRecorded
	m.RecordedAt = &date
	if mw.ClonedFrom != "" {
		m.Model = mw.Model
		m.ClonedFrom = mw.ClonedFrom
		m.Notes = fmt.Sprintf("Recorded %s by cmd/contract-recorder from the %s cases, cloned with -from %s -as %s. "+
			"The allowlists are %s's: drop entries this model no longer needs (the stale check names them) "+
			"and triage new unknown fields and shapes.", date, mw.ClonedFrom, mw.ClonedFrom, mw.Model, mw.ClonedFrom)
		if mw.Synthetic > 0 {
			m.Notes += fmt.Sprintf(" %d not-recordable cases are %s's synthetic bodies, copied unchanged (their case.json notes say so).",
				mw.Synthetic, mw.ClonedFrom)
		}
	}
	return encodeMeta(m)
}

// generatorSources are hand-written files a synthetic response is generated
// from, which a recording makes stale. The bedrock suite builds each binary
// response.eventstream from a response.eventstream.json beside it, and its
// -update regenerates the binary from that source, which would overwrite the
// recording; a recorded body has no source, so the source goes.
var generatorSources = []string{contracttest.ResponseEventStreamFile + ".json"}

// writeRecording saves rec for j: the response file, case.json when rec
// carries one, and, when the case is cloned, its request.json and other
// inputs. It removes any other response file, so the case keeps exactly
// one, and any generator source. It returns the file written and the names
// of the files it removed.
func writeRecording(j job, rec recording) (string, []string, error) {
	if err := os.MkdirAll(j.OutDir, 0o755); err != nil {
		return "", nil, err
	}
	if j.WriteRequest {
		if err := writeInputs(j); err != nil {
			return "", nil, err
		}
	}
	var removed []string
	for _, other := range append(contracttest.ResponseFiles(), generatorSources...) {
		if other == rec.File {
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
	if rec.Spec != nil {
		path := filepath.Join(j.OutDir, contracttest.CaseFile)
		var err error
		if j.SpecRaw != nil && reflect.DeepEqual(*rec.Spec, j.Spec) {
			// Unchanged (a clone's copy): byte for byte, so it diffs
			// cleanly against its source.
			err = os.WriteFile(path, j.SpecRaw, 0o644)
		} else {
			err = writeCaseSpec(path, *rec.Spec)
		}
		if err != nil {
			return "", removed, err
		}
	}
	dst := filepath.Join(j.OutDir, rec.File)
	return dst, removed, os.WriteFile(dst, rec.Body, 0o644)
}

// writeInputs writes a cloned case's request.json and whatever else the
// request needs: upstream_patch.json and the upload.
func writeInputs(j job) error {
	type file struct {
		name string
		data []byte
	}
	files := []file{{contracttest.RequestFile, j.Body}, {PatchFile, j.Patch}}
	// The upload request.json names; not the default one, which needs no file.
	if name, _ := contracttest.InputFileName(j.Endpoint, j.Request); name != "" && j.Input != nil {
		files = append(files, file{j.Input.Name, j.Input.Data})
	}
	for _, f := range files {
		if f.data == nil {
			continue
		}
		if err := os.WriteFile(filepath.Join(j.OutDir, f.name), f.data, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// writeCopy copies a clone's not-recordable case as it is: its inputs, its
// synthetic response body byte for byte, and a case.json whose notes say
// the body was copied, not recorded.
func writeCopy(j job, from, date string) (string, error) {
	if j.RespFile == "" {
		return "", fmt.Errorf("%s has no response file to copy", j.Dir)
	}
	body, err := os.ReadFile(filepath.Join(j.Dir, j.RespFile))
	if err != nil {
		return "", err
	}
	spec := j.Spec
	spec.Notes = fmt.Sprintf("Synthetic, not recorded: copied unchanged from %s/%s by cmd/contract-recorder -from %s -as %s on %s, "+
		"because the case is not recordable; replace the body if %s answers differently. %s",
		from, j.Name, from, j.Target, date, j.Target, j.Spec.Notes)
	if err := os.MkdirAll(j.OutDir, 0o755); err != nil {
		return "", err
	}
	if err := writeInputs(j); err != nil {
		return "", err
	}
	if err := writeCaseSpec(filepath.Join(j.OutDir, contracttest.CaseFile), spec); err != nil {
		return "", err
	}
	dst := filepath.Join(j.OutDir, j.RespFile)
	return dst, os.WriteFile(dst, body, 0o644)
}

// writeCaseSpec writes a case.json, validated as Discover will read it.
func writeCaseSpec(path string, s contracttest.CaseSpec) error {
	if err := s.Validate(); err != nil {
		return fmt.Errorf("%s would be invalid: %v", path, err)
	}
	data, err := encodeIndented(s)
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
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
