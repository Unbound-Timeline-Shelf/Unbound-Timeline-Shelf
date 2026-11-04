import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer, request } from "node:http";
import { testProxyTarget } from "../test_proxy_target.mjs";

test("proxy keeps valid paths and queries on the configured backend", () => {
  assert.deepEqual(testProxyTarget("/api/things?q=a", "http://127.0.0.1:8000"), {
    hostname: "127.0.0.1", port: "8000", path: "/api/things?q=a",
  });
});

test("proxy rejects authority changes and malformed requests", () => {
  for (const target of ["//evil.example/api", "/\\evil.example/api", "http://evil.example/", "api/test", "/api/#fragment"]) {
    assert.equal(testProxyTarget(target, "http://127.0.0.1:8000"), null);
  }
  for (const base of ["", "https://example.com", "http://user:pass@example.com"]) {
    assert.equal(testProxyTarget("/api/test", base), null);
  }
});


test("validated options reach the configured server with the original path", async () => {
  let receivedPath;
  const server = createServer((req, res) => {
    receivedPath = req.url;
    res.end("ok");
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const options = testProxyTarget("/api/items?q=1", `http://127.0.0.1:${server.address().port}`);
    const body = await new Promise((resolve, reject) => {
      request(options, res => {
        let data = "";
        res.on("data", chunk => { data += chunk; });
        res.on("end", () => resolve(data));
      }).on("error", reject).end();
    });
    assert.equal(body, "ok");
    assert.equal(receivedPath, "/api/items?q=1");
  } finally {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
