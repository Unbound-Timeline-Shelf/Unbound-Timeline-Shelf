import assert from "node:assert/strict";
import { test } from "node:test";
import { testProxyTarget } from "../test_proxy_target.mjs";

test("proxy keeps valid paths and queries on the configured backend", () => {
  assert.equal(testProxyTarget("/api/things?q=a", "http://127.0.0.1:8000").href,
    "http://127.0.0.1:8000/api/things?q=a");
});

test("proxy rejects authority changes and malformed requests", () => {
  for (const target of ["//evil.example/api", "/\\evil.example/api", "http://evil.example/", "api/test", "/api/#fragment"]) {
    assert.equal(testProxyTarget(target, "http://127.0.0.1:8000"), null);
  }
  for (const base of ["", "https://example.com", "http://user:pass@example.com"]) {
    assert.equal(testProxyTarget("/api/test", base), null);
  }
});
