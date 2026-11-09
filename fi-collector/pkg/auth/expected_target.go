package auth

import (
	"errors"
	"net/http"
	"strings"

	"google.golang.org/grpc/metadata"
)

const (
	ExpectedOrganizationHeader = "X-FI-Expected-Organization-Id"
	ExpectedWorkspaceHeader    = "X-FI-Expected-Workspace-Id"
	ExpectedProjectHeader      = "X-FI-Expected-Project-Id"
)

var ErrExpectedTargetMismatch = errors.New("expected telemetry target does not match authenticated scope")

// ExpectedTarget is an optional exporter assertion. It is accepted only when all three
// values are present; a partial assertion is a fail-closed authorization error.
type ExpectedTarget struct {
	OrganizationID string
	WorkspaceID    string
	ProjectID      string
}

func ParseExpectedHTTPHeaders(headers http.Header) (*ExpectedTarget, error) {
	return parseExpected(func(name string) []string { return headers.Values(name) })
}

func ParseExpectedMetadata(md metadata.MD) (*ExpectedTarget, error) {
	return parseExpected(func(name string) []string { return md.Get(strings.ToLower(name)) })
}

func parseExpected(values func(string) []string) (*ExpectedTarget, error) {
	fields := []string{ExpectedOrganizationHeader, ExpectedWorkspaceHeader, ExpectedProjectHeader}
	raw := make([]string, len(fields))
	set := 0
	for i := range fields {
		got := values(fields[i])
		if len(got) > 1 {
			return nil, ErrExpectedTargetMismatch
		}
		if len(got) == 1 {
			trimmed := strings.TrimSpace(got[0])
			if trimmed == "" || trimmed != got[0] {
				return nil, ErrExpectedTargetMismatch
			}
			raw[i] = trimmed
			set++
		}
	}
	if set == 0 {
		return nil, nil
	}
	if set != len(fields) {
		return nil, ErrExpectedTargetMismatch
	}
	return &ExpectedTarget{OrganizationID: raw[0], WorkspaceID: raw[1], ProjectID: raw[2]}, nil
}

// CheckScope compares the assertion to the SAME cached result the request will stamp from.
// The headers never choose a workspace and cannot authorize an unauthenticated request.
func (expected *ExpectedTarget) CheckScope(result *ResolveResult) error {
	if expected == nil {
		return nil
	}
	if result == nil || expected.OrganizationID == "" || expected.WorkspaceID == "" || expected.ProjectID == "" ||
		result.OrgID != expected.OrganizationID || result.WorkspaceID != expected.WorkspaceID {
		return ErrExpectedTargetMismatch
	}
	return nil
}
