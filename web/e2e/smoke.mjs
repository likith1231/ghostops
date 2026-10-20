// End-to-end smoke test: the built site against a real backend (mock LLM, real sandbox).
// Expects the site on SITE_URL (default http://localhost:3100) and the backend on :8000.
import puppeteer from "puppeteer-core";

const SITE = process.env.SITE_URL ?? "http://localhost:3100";
const CHROME = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
await page.setViewport({ width: 1440, height: 900 });

const step = (name) => console.log(`✓ ${name}`);
const text = () => page.evaluate(() => document.body.innerText);
const click = (label) =>
  page.evaluate((l) => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent.includes(l) && !x.disabled);
    if (!b) throw new Error(`no enabled button "${l}"`);
    b.click();
  }, label);

try {
  await page.goto(SITE, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForFunction(() => document.body.innerText.includes("ONLINE"), { timeout: 60000 });
  step("site loads and reaches the backend");

  await page.waitForFunction(() => document.querySelectorAll("#usecases h3").length === 17, { timeout: 30000 });
  step("17 use cases from the API");

  await page.evaluate(() => document.querySelector("#pipeline").scrollIntoView());
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.textContent.includes("CI test failure")), { timeout: 30000 });
  await click("CI test failure");
  await click("Trigger incident");
  // The run's own verdict line (other sections also say "rejected").
  await page.waitForFunction(() => /✓ Fix validated|✕ Fix rejected by the safety gates|✕ /.test(document.querySelector("#pipeline").innerText), { timeout: 240000, polling: 500 });
  const verdict = await page.evaluate(() => document.querySelector("#pipeline").innerText);
  if (!verdict.includes("✓ Fix validated")) throw new Error("C4 run was not validated");
  if (!(await text()).includes("ci_test_failure")) throw new Error("diagnosis missing");
  step("C4 runs through the pipeline and the sandbox validates the fix");

  await click("before / after");
  await page.waitForFunction(() => document.body.innerText.includes("BEFORE"), { timeout: 5000 });
  step("before/after diff view");

  await page.keyboard.down("Control");
  await page.keyboard.press("k");
  await page.keyboard.up("Control");
  await page.waitForSelector('input[aria-label="Search commands"]', { timeout: 5000 });
  await page.keyboard.type("detector");
  await page.keyboard.press("Enter");
  step("command palette opens and jumps");

  await page.evaluate(() => document.querySelector("#lab").scrollIntoView());
  await click("SLO breach");
  await page.waitForFunction(() => document.body.innerText.includes("Error-rate SLO breach"), { timeout: 10000 });
  step("detector lab runs the detectors");

  await page.evaluate(() => document.querySelector("#incidents").scrollIntoView());
  await page.waitForSelector("#incidents button", { timeout: 10000 });
  await page.evaluate(() => document.querySelector("#incidents button").click());
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.textContent.includes("Replay")), { timeout: 10000 });
  await click("Replay");
  await page.waitForSelector('[role="dialog"]', { timeout: 5000 });
  step("incident replay opens");

  const fatal = errors.filter((e) => !/WebGL|GPU stall|THREE\./.test(e));
  if (fatal.length) throw new Error(`page errors: ${fatal.join(" | ")}`);
  step("no page errors");
} catch (err) {
  console.error(`✗ ${err.message}`);
  await page.screenshot({ path: "e2e-failure.png" }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
