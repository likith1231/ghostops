import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

function respond(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status, statusText: status === 409 ? "Conflict" : "OK" })));
}

describe("api client", () => {
  it("returns parsed JSON", async () => {
    respond(200, { mock_llm: true, pipeline_busy: false });
    await expect(api.status()).resolves.toEqual({ mock_llm: true, pipeline_busy: false });
  });

  it("surfaces the backend's detail message on errors", async () => {
    respond(409, { detail: "a pipeline is already running" });
    await expect(api.simulate("b1")).rejects.toThrow("a pipeline is already running");
  });

  it("falls back to the status line when the error body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("oops", { status: 502, statusText: "Bad Gateway" })));
    await expect(api.summary()).rejects.toThrow("502 Bad Gateway");
  });

  it("posts the scenario as JSON", async () => {
    respond(202, { run_id: "abc", mock_llm: true });
    await api.simulate("c4");
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toMatch(/\/api\/simulate$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ scenario: "c4" });
  });
});
