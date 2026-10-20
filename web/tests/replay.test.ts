import { describe, expect, it } from "vitest";
import { parts } from "@/components/Replay";

describe("Replay parts", () => {
  it("splits the stored outcome summary into its labelled pieces", () => {
    const p = parts("Diagnosis: leak in /leak | Validation: PASS | (All 5 tests passed.) | PR: https://github.com/x/y/pull/3");
    expect(p).toEqual({ Diagnosis: "leak in /leak", Validation: "PASS", detail: "All 5 tests passed.", PR: "https://github.com/x/y/pull/3" });
  });

  it("recognises a skipped PR", () => {
    expect(parts("Validation: FAIL | PR skipped: validation_failed")["PR skipped"]).toBe("validation_failed");
  });

  it("copes with an empty summary", () => {
    expect(parts(null)).toEqual({});
  });
});
