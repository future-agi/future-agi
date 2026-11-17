"use strict";
// A defective guard must fail its control without reaching DNS or a socket.
const net = require("node:net");
const dns = require("node:dns");
const state = { primitiveReached: 0 };
globalThis.__editorPacketFree = state;
function sentinel() {
  state.primitiveReached += 1;
  throw new Error("EDITOR_ORIGINAL_PRIMITIVE_REACHED");
}
net.Socket.prototype.connect = sentinel;
for (const target of [
  dns,
  dns.Resolver.prototype,
  dns.promises,
  dns.promises.Resolver.prototype,
]) {
  for (const name of Object.getOwnPropertyNames(target)) {
    if (
      /^(lookup|resolve|reverse)/.test(name) &&
      typeof target[name] === "function"
    )
      target[name] = sentinel;
  }
}
require(process.env.EVAL_GUARD_IMPLEMENTATION || "./node-guard.cjs");
