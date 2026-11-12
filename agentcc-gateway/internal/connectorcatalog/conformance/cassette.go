package conformance

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path"
	"strings"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

// cassetteSuffix names a recorded cassette among a provider's fixtures.
const cassetteSuffix = ".cassette.json"

// recording is what a provider's MCP cassette gives the mock: the tools its
// recorded tools/list served, each with its first recorded call's result and
// arguments, and the protocol version it was recorded under.
type recording struct {
	File  string
	Era   string
	Tools []*Tool
}

// readRecording finds the provider's MCP cassette under
// apps/<app>/providers/<provider>/fixtures/ (any depth, *.cassette.json):
// nil when it has none, an error when it has more than one or one that is
// not a clean MCP cassette. An HTTP cassette is a K2 runtime's input, not the
// gateway's upstream, and is left alone.
func readRecording(fsys fs.FS, app, provider string) (*recording, error) {
	dir := path.Join("apps", app, "providers", provider, "fixtures")
	var found []*recording
	err := fs.WalkDir(fsys, dir, func(p string, d fs.DirEntry, err error) error {
		if errors.Is(err, fs.ErrNotExist) && p == dir {
			return fs.SkipDir
		}
		if err != nil || d.IsDir() || !strings.HasSuffix(p, cassetteSuffix) {
			return err
		}
		data, err := fs.ReadFile(fsys, p)
		if err != nil {
			return err
		}
		if err := cassette.Check(data); err != nil {
			return fmt.Errorf("%s: %w", p, err)
		}
		c, err := cassette.Load(data)
		if err != nil {
			return fmt.Errorf("%s: %w", p, err)
		}
		if c.Kind != cassette.KindMCP {
			return nil
		}
		rec, err := fromCassette(p, c.MCP)
		if err != nil {
			return err
		}
		found = append(found, rec)
		return nil
	})
	if err != nil {
		return nil, err
	}
	switch len(found) {
	case 0:
		return nil, nil
	case 1:
		return found[0], nil
	}
	files := make([]string, len(found))
	for i, r := range found {
		files[i] = r.File
	}
	return nil, fmt.Errorf("%s: %d MCP cassettes (%s); L3 serves one recorded tools/list", dir, len(found), strings.Join(files, ", "))
}

// mockKeys are keys the mock reads from a tools.json entry itself, so a
// recorded definition must not carry them.
var mockKeys = []string{"price_usd", "result"}

func fromCassette(file string, rec *cassette.MCPRecording) (*recording, error) {
	out := &recording{File: file, Era: rec.ProtocolVersion}
	byName := map[string]*Tool{}
	listed := false
	for i, x := range rec.Exchanges {
		switch {
		case x.Method == "tools/list" && len(x.Result) > 0:
			listed = true
			var page struct {
				Tools []json.RawMessage `json:"tools"`
			}
			if err := json.Unmarshal(x.Result, &page); err != nil {
				return nil, fmt.Errorf("%s: exchange %d (tools/list): %w", file, i, err)
			}
			for _, def := range page.Tools {
				t, err := recordedTool(def)
				if err != nil {
					return nil, fmt.Errorf("%s: exchange %d (tools/list): %w", file, i, err)
				}
				if byName[t.Name] != nil {
					return nil, fmt.Errorf("%s: tools/list serves %s twice", file, t.Name)
				}
				byName[t.Name] = t
				out.Tools = append(out.Tools, t)
			}
		case x.Method == "tools/call" && len(x.Result) > 0:
			var call struct {
				Name      string          `json:"name"`
				Arguments json.RawMessage `json:"arguments"`
			}
			if err := json.Unmarshal(x.Params, &call); err != nil {
				return nil, fmt.Errorf("%s: exchange %d (tools/call): %w", file, i, err)
			}
			if t := byName[call.Name]; t != nil && t.result == nil {
				t.result = x.Result
				if len(call.Arguments) > 0 {
					t.Arguments = call.Arguments
				}
				var res struct {
					IsError bool `json:"isError"`
				}
				_ = json.Unmarshal(x.Result, &res)
				t.ResultIsError = res.IsError
			}
		}
	}
	if !listed {
		return nil, fmt.Errorf("%s: records no tools/list result", file)
	}
	return out, nil
}

func recordedTool(def json.RawMessage) (*Tool, error) {
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(def, &obj); err != nil {
		return nil, err
	}
	var name string
	if err := json.Unmarshal(obj["name"], &name); err != nil || name == "" {
		return nil, errors.New("a recorded tool has no name")
	}
	for _, key := range mockKeys {
		if _, ok := obj[key]; ok {
			return nil, fmt.Errorf("recorded tool %s carries %q, which agentcc-mcpmock reads as its own", name, key)
		}
	}
	return &Tool{Name: name, def: def, Hints: servedHints(def), Arguments: exampleArguments(obj["inputSchema"])}, nil
}
