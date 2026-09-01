#!/usr/bin/env node

import { mkdir, readdir, rename, stat } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium, chromiumLaunchOptions } from "./browser_runtime.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const evidenceDir = resolve(root, process.env.SPEECH_EVAL_WEB_EVIDENCE_DIR || "var/evidence/web");
const demoStart = process.env.SPEECH_EVAL_DEMO_START || join(root, "web", "index.html");
const videoDir = join(evidenceDir, "video-tmp-final");
const downloadDir = join(evidenceDir, "downloads");
await mkdir(videoDir, { recursive: true });
await mkdir(downloadDir, { recursive: true });

const browser = await chromium.launch(chromiumLaunchOptions({
  headless: true,
}));
const context = await browser.newContext({
  acceptDownloads: true,
  locale: "zh-CN",
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: videoDir, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();

async function pause(milliseconds = 900) {
  await page.waitForTimeout(milliseconds);
}

await page.goto(pathToFileURL(resolve(root, demoStart)).href, {
  waitUntil: "load",
});
await pause(2200);
await page.locator("article:last-child").scrollIntoViewIfNeeded();
await pause(1600);

await page.goto("http://127.0.0.1:18765/", { waitUntil: "networkidle" });
await page.locator("#health-badge.health-badge--ok").waitFor();
await page.getByText("110", { exact: true }).first().waitFor();
await pause(1200);
await page.screenshot({ path: join(evidenceDir, "web-01-dashboard.png") });

await page.locator("#filter-q").fill("SYN-0001");
await page.locator("#filter-form button[type='submit']").click();
await page.locator(".result-item[data-sample-id='SYN-0001']").waitFor();
await page.locator(".result-item[data-sample-id='SYN-0001']").click();
await page.locator("#detail-sample-id").getByText("SYN-0001", { exact: true }).waitFor();
await page.locator("#detail-content").scrollIntoViewIfNeeded();
await pause(1200);
await page.screenshot({ path: join(evidenceDir, "web-02-syn-0001-detail.png") });

await page.locator("#evidence-heading").scrollIntoViewIfNeeded();
await pause(1000);
await page.screenshot({ path: join(evidenceDir, "web-03-metric-evidence.png") });

await page.locator("#revision-heading").scrollIntoViewIfNeeded();
await pause(1000);
await page.screenshot({ path: join(evidenceDir, "web-04-revision-audit.png") });

await page.locator("#filter-form").scrollIntoViewIfNeeded();
const downloadPromise = page.waitForEvent("download");
await page.locator("#download-json").click();
const download = await downloadPromise;
await download.saveAs(join(downloadDir, download.suggestedFilename()));
await pause(900);

const reportPath = resolve(root, process.env.SPEECH_EVAL_REPORT || "exports/evaluation-summary.html");
await page.goto(pathToFileURL(reportPath).href, { waitUntil: "load" });
await pause(1400);
await page.screenshot({
  path: join(evidenceDir, "web-05-evaluation-summary.png"),
  fullPage: true,
});

await context.close();
await browser.close();

const videos = (await readdir(videoDir)).filter((name) => name.endsWith(".webm"));
if (!videos.length) throw new Error("no WebM recording was created");
const ranked = await Promise.all(
  videos.map(async (name) => ({ name, size: (await stat(join(videoDir, name))).size })),
);
ranked.sort((left, right) => right.size - left.size);
await rename(
  join(videoDir, ranked[0].name),
  join(evidenceDir, "complete-chain-demo-20260824.webm"),
);
