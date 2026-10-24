package auth

import (
	"fmt"
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"

	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc/metadata"
)

// ProjectNameHeader names the Observe project for exporters that can set
// request headers but not resource attributes (OpenRouter Broadcast, vendor
// telemetry drains, edge runtimes). gRPC carries it as metadata under the same
// name; gRPC metadata keys are lowercase, so that is x-project-name.
//
// ProjectNameHeaderAlias is read too, because traceAI's Java and C# exporters
// already send FI-Project-Name (and traceAI's agno, beeai and strands
// instrumentors already send x-project-name). Both headers name one project:
// when both are sent, they must agree.
//
// Precedence, per ResourceSpans: a non-empty string resource attribute
// `project_name` wins; otherwise the header value is written into
// `project_name`; otherwise the request behaves as before (rejected when auth
// is on, because every ResourceSpans needs a project). A name that came from
// the header is then resolved, workspace-scoped and auto-created exactly like
// one that came from the attribute, because StampResourceAttrs only ever reads
// the attribute.
//
// Each header carries one name, once. RFC 9110 section 5.3 lets a proxy join
// repeated field lines into one comma-separated value, so a value with a comma
// and a header sent twice are the same thing on the wire: both are rejected,
// rather than guessing which name was meant.
const (
	ProjectNameHeader      = "X-Project-Name"
	ProjectNameHeaderAlias = "FI-Project-Name"
)

// resourceProjectNameKey is the resource attribute StampResourceAttrs reads.
const resourceProjectNameKey = "project_name"

// maxProjectNameChars is the longest name the header may carry: the project
// table's name column holds 255 characters, so a longer name could never be
// matched or created, and its spans would be dropped without an error the
// sender sees.
const maxProjectNameChars = 255

// ProjectNameValues holds a request's project header field lines: those of
// ProjectNameHeader, then those of ProjectNameHeaderAlias.
type ProjectNameValues struct {
	Canonical, Alias []string
}

// ProjectNameHTTPValues returns the project header field lines of an
// OTLP/HTTP request.
func ProjectNameHTTPValues(h http.Header) ProjectNameValues {
	return ProjectNameValues{h.Values(ProjectNameHeader), h.Values(ProjectNameHeaderAlias)}
}

// ProjectNameMetadataValues returns the project metadata values of an
// OTLP/gRPC call.
func ProjectNameMetadataValues(md metadata.MD) ProjectNameValues {
	return ProjectNameValues{md.Get(ProjectNameHeader), md.Get(ProjectNameHeaderAlias)}
}

// ApplyProjectNameHeader writes the project named by the header into the
// `project_name` resource attribute of every ResourceSpans that has no usable
// one: absent, empty, or not a string (the cases StampResourceAttrs counts as
// missing). A ResourceSpans that names its own project keeps it.
//
// The header is only read when some ResourceSpans needs it, so a request whose
// resources all name a project is unaffected by the header, whatever it holds.
// When it is needed, the value is trimmed and a blank one is ignored. It is an
// error for a header to be sent more than once or to hold a comma (a list),
// for the two headers to disagree, or for the value to be invalid UTF-8,
// longer than 255 characters, or to contain a control character, a line or
// paragraph separator (U+2028, U+2029), or an invisible character: a format
// character (Unicode category Cf, such as U+200B) or another default-ignorable
// code point (such as the Hangul filler U+3164 or a variation selector), or to
// have no visible character at all (only combining marks and blank symbols
// such as U+2800). It returns how many ResourceSpans took the header's project.
func ApplyProjectNameHeader(traces ptrace.Traces, values ProjectNameValues) (int, error) {
	rss := traces.ResourceSpans()
	need := false
	for i := 0; i < rss.Len() && !need; i++ {
		need = getStrAttr(rss.At(i).Resource().Attributes(), resourceProjectNameKey) == ""
	}
	if !need || len(values.Canonical)+len(values.Alias) == 0 {
		return 0, nil
	}
	name, err := projectNameFromHeaderValues(values)
	if err != nil || name == "" {
		return 0, err
	}
	applied := 0
	for i := 0; i < rss.Len(); i++ {
		attrs := rss.At(i).Resource().Attributes()
		if getStrAttr(attrs, resourceProjectNameKey) != "" {
			continue
		}
		attrs.PutStr(resourceProjectNameKey, name)
		applied++
	}
	return applied, nil
}

func projectNameFromHeaderValues(values ProjectNameValues) (string, error) {
	canonical, err := projectNameFromHeader(ProjectNameHeader, values.Canonical)
	if err != nil {
		return "", err
	}
	alias, err := projectNameFromHeader(ProjectNameHeaderAlias, values.Alias)
	if err != nil {
		return "", err
	}
	if canonical != "" && alias != "" && canonical != alias {
		return "", fmt.Errorf("conflicting project names in %s and %s", ProjectNameHeader, ProjectNameHeaderAlias)
	}
	if canonical != "" {
		return canonical, nil
	}
	return alias, nil
}

// projectNameFromHeader validates the field lines of one project header and
// returns its trimmed value ("" when absent or blank).
func projectNameFromHeader(header string, lines []string) (string, error) {
	switch {
	case len(lines) == 0:
		return "", nil
	case len(lines) > 1:
		return "", fmt.Errorf("%s was sent %d times; send it once, with one project name", header, len(lines))
	}
	v := strings.TrimSpace(lines[0])
	switch {
	case v == "":
		return "", nil
	case !utf8.ValidString(v):
		return "", fmt.Errorf("%s is not valid UTF-8", header)
	case strings.ContainsRune(v, ','):
		return "", fmt.Errorf("%s holds a comma-separated list; send one project name without commas", header)
	case utf8.RuneCountInString(v) > maxProjectNameChars:
		return "", fmt.Errorf("%s is longer than %d characters", header, maxProjectNameChars)
	}
	for _, r := range v {
		switch {
		case unicode.IsControl(r):
			return "", fmt.Errorf("%s contains a control character (%U)", header, r)
		case unicode.Is(unicode.Cf, r):
			return "", fmt.Errorf("%s contains an invisible format character (%U)", header, r)
		case unicode.In(r, unicode.Zl, unicode.Zp):
			return "", fmt.Errorf("%s contains a line or paragraph separator (%U)", header, r)
		case unicode.In(r, unicode.Other_Default_Ignorable_Code_Point, unicode.Variation_Selector):
			return "", fmt.Errorf("%s contains an invisible character (%U)", header, r)
		}
	}
	if !hasVisibleRune(v) {
		return "", fmt.Errorf("%s has no visible character", header)
	}
	return v, nil
}

// hasVisibleRune reports whether v has a character that is drawn as something:
// not a space, not a combining mark (which needs a base character to sit on),
// and not a symbol drawn as empty space. Inside a visible name those are kept,
// like an inner space.
func hasVisibleRune(v string) bool {
	for _, r := range v {
		blank := unicode.IsSpace(r) || unicode.Is(unicode.M, r) ||
			r == '\u2800' || r == '\U0001D159' // BRAILLE PATTERN BLANK, MUSICAL SYMBOL NULL NOTEHEAD
		if !blank {
			return true
		}
	}
	return false
}
