/**
 * Resolve the optional Playwright/browser runtime without embedding a
 * developer-machine path in the public repository.
 *
 * Set SPEECH_EVAL_PLAYWRIGHT_MODULE (or PLAYWRIGHT_MODULE) to an absolute
 * module path when Playwright is provided by a managed runtime. Otherwise a
 * normal project installation ("npm install playwright") is used.
 */

import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);

function loadPlaywright() {
  const candidates = [
    process.env.SPEECH_EVAL_PLAYWRIGHT_MODULE,
    process.env.PLAYWRIGHT_MODULE,
    "playwright",
  ].filter(Boolean);
  let lastError;
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (error) {
      lastError = error;
    }
  }
  const detail = lastError?.message ? ` Last error: ${lastError.message}` : "";
  throw new Error(
    "Playwright is required for browser capture. Install it or set "
      + "SPEECH_EVAL_PLAYWRIGHT_MODULE."
      + detail,
  );
}

export const { chromium } = loadPlaywright();

/** Return an explicitly configured browser executable, then common Edge paths. */
export function browserExecutablePath() {
  const configured = process.env.SPEECH_EVAL_BROWSER_EXECUTABLE
    || process.env.BROWSER_EXECUTABLE_PATH;
  if (configured) return configured;
  if (process.platform !== "win32") return undefined;
  const localAppData = process.env.LOCALAPPDATA;
  const candidates = [
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    localAppData ? join(localAppData, "Microsoft", "Edge", "Application", "msedge.exe") : null,
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate));
}

export function chromiumLaunchOptions(options = {}) {
  const executablePath = browserExecutablePath();
  return executablePath ? { ...options, executablePath } : options;
}
