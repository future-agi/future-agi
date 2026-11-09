// Package credtemplate places a pushed MCP upstream credential where the
// vendor takes it (CONNECTORS-PLAN.md CN7; AM-C9, AM-C12 a, AM-C20): in a
// header, in a query parameter, or in a secret {variable} of the server
// URL's path or query, in one part or in several, with the non-secret
// constant parts a catalog provider declares.
//
// A Template is compiled once per upstream server, from the server's URL
// and the credential's parts (ForSecret, ForCatalogSecret, Compile), and
// wraps the HTTP transport the server's client sends through
// (Template.RoundTripper). Each request to the server's origin goes out as
// a copy that carries every part; a request to any other origin passes
// through unchanged, so no part leaves the origin it was configured for.
// The caller's request, the URL the client keeps, the errors it gets back,
// and a Location or an HTTP+SSE endpoint an upstream echoes back all hold
// the template's form ({name} placeholders, no key), never a secret value.
//
// Every rule is checked when a template is compiled, so a placement the
// gateway cannot apply is refused before anything is sent (AM-C20 item 2);
// an *Error names the field, never a value. The package imports only the
// standard library, so the org-config check (tenant) and the MCP pool
// (tenancy) can share its rules; its catalogparts subpackage adapts a
// connector-manifest/v1 catalog (CN1) to Catalog.
package credtemplate
