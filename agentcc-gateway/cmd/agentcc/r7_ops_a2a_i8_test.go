package main

// r7A2ATaskCarriesClientIP says whether an A2A task's model call carries the
// client IP of the A2A request that started it. Round 7 integrator line I8
// (internal/a2a/server.go taskContext, internal/middleware/clientip.go
// WithClientIP) makes it so: the task's chat request carries the A2A
// request's resolved client IP, so every IP rule of the pipeline (an org's
// ip_acl, the node's, a key's allowed_ips) judges the caller's address. The
// tests that read it state both behaviours, so setting it back to false
// without removing the line fails them.
const r7A2ATaskCarriesClientIP = true
