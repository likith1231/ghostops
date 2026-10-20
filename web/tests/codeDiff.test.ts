import { describe, expect, it } from "vitest";
import { splitRows } from "@/components/CodeDiff";

const DIFF = `--- a/sample-app/math_utils.py
+++ b/sample-app/math_utils.py
@@ -4,3 +4,3 @@
 
 def multiply(a, b):
-    return a + b
+    return a * b
`;

describe("splitRows", () => {
  it("pairs a removed line with its replacement and keeps context on both sides", () => {
    const rows = splitRows(DIFF);
    expect(rows[0]).toEqual({ hunk: "@@ -4,3 +4,3 @@" });
    const change = rows.find((r) => "left" in r && r.left?.kind === "del");
    expect(change).toEqual({
      left: { n: 6, text: "    return a + b", kind: "del" },
      right: { n: 6, text: "    return a * b", kind: "add" },
    });
    const ctx = rows.filter((r) => "left" in r && r.left?.kind === "ctx");
    expect(ctx.map((r) => "left" in r && r.left?.n)).toEqual([4, 5]);
  });

  it("leaves the other side empty for pure additions", () => {
    const rows = splitRows("@@ -1,1 +1,3 @@\n x = 1\n+y = 2\n+z = 3\n");
    const adds = rows.filter((r) => "right" in r && r.right?.kind === "add");
    expect(adds).toHaveLength(2);
    expect(adds.every((r) => "left" in r && r.left === undefined)).toBe(true);
    expect(adds.map((r) => "right" in r && r.right?.n)).toEqual([2, 3]);
  });

  it("restarts line numbers at each hunk", () => {
    const rows = splitRows("@@ -1,1 +1,1 @@\n-a\n+b\n@@ -40,1 +40,1 @@\n-c\n+d\n");
    const changes = rows.filter((r) => "left" in r);
    expect(changes.map((r) => "left" in r && r.left?.n)).toEqual([1, 40]);
  });

  it("returns nothing for an empty diff", () => {
    expect(splitRows("")).toEqual([]);
  });
});
