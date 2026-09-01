#!/usr/bin/env node

/** Seek through a local WebM with the same Edge/Playwright stack used to record it. */

import { createHash } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { basename, dirname, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium, chromiumLaunchOptions } from "./browser_runtime.mjs";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = resolve(scriptDir, "..");
const videoPath = resolve(root, process.argv[2] || "");
const outputDir = resolve(root, process.argv[3] || "var/evidence/video-visual-review");
const requested = (process.argv[4] || "3,18,36,55,75,95")
  .split(",")
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isFinite(value) && value >= 0);

if (!process.argv[2]) {
  throw new Error("usage: inspect_recording.mjs <video> <output-dir> [seconds,...]");
}
await mkdir(outputDir, { recursive: true });

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex").toUpperCase();
}

const browser = await chromium.launch(chromiumLaunchOptions({
  headless: true,
}));

const frames = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(pathToFileURL(videoPath).href, { waitUntil: "domcontentloaded" });
  await page.locator("video").waitFor({ state: "attached", timeout: 30000 });
  const duration = await page.locator("video").evaluate(async (video) => {
    if (Number.isFinite(video.duration) && video.duration > 0) return video.duration;
    await new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => rejectPromise(new Error("video metadata timeout")), 30000);
      video.addEventListener("loadedmetadata", () => {
        clearTimeout(timer);
        resolvePromise();
      }, { once: true });
    });
    return video.duration;
  });
  const seconds = [...new Set([...requested, Math.max(0, duration - 2)])]
    .map((value) => Math.min(value, Math.max(0, duration - 0.05)))
    .sort((left, right) => left - right);
  for (const second of seconds) {
    await page.locator("video").evaluate(async (video, target) => {
      video.pause();
      if (Math.abs(video.currentTime - target) < 0.01 && video.readyState >= 2) return;
      await new Promise((resolvePromise, rejectPromise) => {
        const timer = setTimeout(() => rejectPromise(new Error(`seek timeout at ${target}`)), 30000);
        const done = () => {
          clearTimeout(timer);
          resolvePromise();
        };
        video.addEventListener("seeked", done, { once: true });
        video.currentTime = target;
      });
    }, second);
    await page.waitForTimeout(250);
    const filename = `frame-${String(Math.round(second * 10)).padStart(5, "0")}.png`;
    const path = resolve(outputDir, filename);
    await page.screenshot({ path });
    const buffer = await readFile(path);
    frames.push({ second, file: filename, bytes: buffer.length, sha256: sha256(buffer) });
  }
  const videoBuffer = await readFile(videoPath);
  const videoStat = await stat(videoPath);
  const report = {
    inspected_with: "Microsoft Edge + Playwright local playback",
    video: {
      path: videoPath,
      name: basename(videoPath),
      bytes: videoStat.size,
      sha256: sha256(videoBuffer),
      duration_seconds: duration,
    },
    viewport: { width: 1440, height: 900 },
    frames,
  };
  const reviewPath = resolve(outputDir, "visual-review.json");
  await writeFile(reviewPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");

  // Visual review happens after the recorder finalizes its own artifact
  // manifest. Extend that manifest so every reviewed frame and the review
  // report are protected by the same tamper-evident inventory.
  const artifactPath = resolve(dirname(videoPath), "artifact-sha256.json");
  try {
    const artifact = JSON.parse(await readFile(artifactPath, "utf8"));
    artifact.files ||= {};
    for (const frame of frames) {
      const framePath = resolve(outputDir, frame.file);
      artifact.files[relative(root, framePath).replaceAll("\\", "/")] = sha256(await readFile(framePath));
    }
    artifact.files[relative(root, reviewPath).replaceAll("\\", "/")] = sha256(await readFile(reviewPath));
    artifact.visual_review_frame_count = frames.length;
    artifact.note = "artifact-sha256.json intentionally excludes its own digest";
    await writeFile(artifactPath, `${JSON.stringify(artifact, null, 2)}\n`, "utf8");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
} finally {
  await browser.close();
}
