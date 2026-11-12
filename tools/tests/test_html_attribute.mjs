import assert from "node:assert/strict";
import { test } from "node:test";
import { escapeHtmlAttribute } from "../html_attribute.mjs";

test("escapes markup and both quote types in an attribute value", () => {
  assert.equal(escapeHtmlAttribute("x\" autofocus onfocus=\"alert(1)<&'"),
    "x&quot; autofocus onfocus=&quot;alert(1)&lt;&amp;&#39;");
});
