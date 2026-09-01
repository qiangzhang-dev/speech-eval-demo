#!/usr/bin/env node

/** Record one uncut browser session around a genuinely executed fresh run. */

import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { appendFile, mkdir, readFile, readdir, rename, stat, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium, chromiumLaunchOptions } from "./browser_runtime.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const evidenceRoot = process.env.SPEECH_EVAL_EVIDENCE_DIR
  ? resolve(root, process.env.SPEECH_EVAL_EVIDENCE_DIR)
  : join(root, "var", "evidence");
const dateDir = join(evidenceRoot, "real-experiment");
const timestamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "-");
const experimentId = process.argv[2] || `REAL-${timestamp}`;
if (!/^[A-Z0-9][A-Z0-9-]{7,80}$/.test(experimentId)) {
  throw new Error("experiment ID must contain only uppercase letters, digits, and hyphens");
}
const batchId = `BATCH-${experimentId}`;
const runId = `RUN-${experimentId}`;
const runDir = join(dateDir, experimentId);
const videoTmp = join(runDir, "video-tmp");
const downloadsDir = join(runDir, "browser-downloads");
const finalVideo = join(runDir, `real-experiment-demo-${experimentId}.webm`);
const serverLog = join(runDir, "live-server.log");
const journeyPath = join(runDir, "recording-journey.json");
const serverPort = Number(process.env.REAL_EXPERIMENT_PORT || 18766);
const workbenchPort = Number(process.env.REAL_WORKBENCH_PORT || 18767);
const pythonExe = process.env.PYTHON_EXE || "python";

await mkdir(videoTmp, { recursive: true });
await mkdir(downloadsDir, { recursive: true });

function pause(page, milliseconds = 1000) {
  return page.waitForTimeout(milliseconds);
}

function lineReader(stream, onLine) {
  let pending = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    pending += chunk;
    while (pending.includes("\n")) {
      const index = pending.indexOf("\n");
      const line = pending.slice(0, index).replace(/\r$/, "");
      pending = pending.slice(index + 1);
      onLine(line);
    }
  });
}

async function waitForReady(process, timeoutMs = 30000) {
  return new Promise((resolvePromise, rejectPromise) => {
    const timer = setTimeout(
      () => rejectPromise(new Error("live experiment server did not become ready")),
      timeoutMs,
    );
    let settled = false;
    lineReader(process.stdout, async (line) => {
      await appendFile(serverLog, `${line}\n`, "utf8");
      if (settled) return;
      try {
        const payload = JSON.parse(line);
        if (payload.ready === true) {
          settled = true;
          clearTimeout(timer);
          resolvePromise(payload);
        }
      } catch (_error) {
        // Non-JSON diagnostic output stays in live-server.log.
      }
    });
    lineReader(process.stderr, (line) => appendFile(serverLog, `[stderr] ${line}\n`, "utf8"));
    process.once("exit", (code) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        rejectPromise(new Error(`live experiment server exited early: ${code}`));
      }
    });
  });
}

async function overlay(page, title, detail) {
  await page.evaluate(
    ({ titleText, detailText }) => {
      let node = document.querySelector("#recording-step-overlay");
      if (!node) {
        node = document.createElement("aside");
        node.id = "recording-step-overlay";
        Object.assign(node.style, {
          position: "fixed", zIndex: "2147483647", left: "24px", bottom: "22px",
          maxWidth: "760px", padding: "12px 18px", border: "1px solid rgba(117,224,255,.75)",
          borderRadius: "12px", background: "rgba(3,13,24,.94)",
          boxShadow: "0 12px 50px rgba(0,0,0,.55)", color: "#f1f8ff",
          fontFamily: '"Microsoft YaHei", sans-serif', pointerEvents: "none",
        });
        document.body.appendChild(node);
      }
      node.innerHTML = "";
      const strong = document.createElement("strong");
      strong.textContent = titleText;
      Object.assign(strong.style, { display: "block", color: "#55d7ff", fontSize: "18px" });
      const span = document.createElement("span");
      span.textContent = detailText;
      Object.assign(span.style, { display: "block", marginTop: "3px", fontSize: "14px" });
      node.append(strong, span);
    },
    { titleText: title, detailText: detail },
  );
}

async function saveDownload(download, label) {
  const filename = download.suggestedFilename();
  const target = join(downloadsDir, filename);
  await download.saveAs(target);
  const size = (await stat(target)).size;
  return { label, filename, path: relative(root, target).replaceAll("\\", "/"), size };
}

async function hashFile(path) {
  const digest = createHash("sha256");
  digest.update(await readFile(path));
  return digest.digest("hex").toUpperCase();
}

const journey = {
  schema_version: "single-take-browser-journey-v1",
  experiment_id: experimentId,
  batch_id: batchId,
  run_id: runId,
  recording_started_at: new Date().toISOString(),
  uncut: true,
  browser: "Microsoft Edge via Playwright",
  viewport: { width: 1440, height: 900 },
  actions: [],
  downloads: [],
};

function action(name, details = {}) {
  journey.actions.push({ at: new Date().toISOString(), name, ...details });
}

const server = spawn(
  pythonExe,
  [
    "scripts/live_experiment_server.py", "--experiment-id", experimentId,
    "--port", String(serverPort), "--workbench-port", String(workbenchPort),
    "--min-step-seconds", "2.8",
  ],
  {
    cwd: root,
    env: { ...process.env, PYTHONPATH: join(root, "src"), PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  },
);

let browser;
let context;
let page;
try {
  await waitForReady(server);
  browser = await chromium.launch(chromiumLaunchOptions({
    headless: true,
  }));
  context = await browser.newContext({
    acceptDownloads: true, locale: "zh-CN", viewport: { width: 1440, height: 900 },
    recordVideo: { dir: videoTmp, size: { width: 1440, height: 900 } },
  });
  page = await context.newPage();

  action("recording_started_before_experiment");
  await page.goto(`http://127.0.0.1:${serverPort}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#start").waitFor();
  await pause(page, 2800);
  action("fresh_database_precondition_shown");

  await page.locator("#start").click();
  action("clicked_start_real_experiment");
  await page.locator('body[data-status="awaiting_revision"]').waitFor({ timeout: 300000 });
  await pause(page, 3500);
  action("real_ai_stage_completed", { provider: "codex_cli", model: "gpt-5.6-sol" });

  await page.goto(`http://127.0.0.1:${workbenchPort}/`, { waitUntil: "networkidle" });
  await page.locator("#health-badge.health-badge--ok").waitFor({ timeout: 30000 });
  await page.getByText("110", { exact: true }).first().waitFor();
  await overlay(page, "步骤 6 · 打开本次新数据库工作台", `${batchId} / ${runId} · 110 条结果均来自刚才现场执行；前一步已审计 C01/C02/C03 阶段链路`);
  await pause(page, 3600);
  action("opened_current_run_workbench");

  await page.locator('[name="batch_id"]').fill(batchId);
  await page.locator('[name="run_id"]').fill(runId);
  await page.locator("#filter-q").fill("SYN-0001");
  await page.locator("#filter-form button[type='submit']").click();
  await page.locator(".result-item[data-sample-id='SYN-0001']").waitFor();
  await page.locator(".result-item[data-sample-id='SYN-0001']").click();
  await page.locator("#detail-sample-id").getByText("SYN-0001", { exact: true }).waitFor();
  await overlay(page, "步骤 6 · 查看 C01 / SYN-0001", "先核对语音识别参考文本、系统转写、CER、候选阈值与证据；未生效阈值只显示‘证据不足’。");
  await page.locator("#metadata-heading").scrollIntoViewIfNeeded();
  await pause(page, 3200);
  await page.locator("#fields-heading").scrollIntoViewIfNeeded();
  await pause(page, 3400);
  action("inspected_syn_0001_full_result", { capability_chain: "C01", stages: ["transcript"] });

  await page.locator("#evidence-heading").scrollIntoViewIfNeeded();
  await overlay(page, "步骤 6 · C01 指标证据闭环", "查看 CER 指标快照、证据 ID、来源与定位信息；AI 诊断不得改变参考文本、系统输出和 CER。");
  await pause(page, 3600);
  action("inspected_metric_evidence");

  // Continue in the same browser session and inspect the downstream chains.
  await page.locator("#filter-form").scrollIntoViewIfNeeded();
  await page.locator("#filter-q").fill("SYN-0003");
  await page.locator("#filter-form button[type='submit']").click();
  await page.locator(".result-item[data-sample-id='SYN-0003']").waitFor();
  await page.locator(".result-item[data-sample-id='SYN-0003']").click();
  await page.locator("#detail-sample-id").getByText("SYN-0003", { exact: true }).waitFor();
  await overlay(page, "步骤 6 · 查看 C02 / SYN-0003 机器翻译", "同一批次中的机器翻译链路：核对中文转写、英文参考译文、系统译文、translation_exact_match 阶段指标和阶段证据。");
  await page.locator("#fields-heading").scrollIntoViewIfNeeded();
  await page.locator("#field-grid").getByText("参考文本/标注", { exact: true }).waitFor();
  await page.locator("#field-grid").getByText("系统输出", { exact: true }).waitFor();
  await pause(page, 3000);
  await page.locator("#evidence-heading").scrollIntoViewIfNeeded();
  await page.locator("#evidence-list").getByText("E-SYN-0003-STAGE-01-machine_translation", { exact: true }).waitFor();
  await overlay(page, "步骤 6 · C02 阶段证据", "阶段证据同时保存 reference/output，并由质量诊断与最终结论引用；不是只靠 CER 标签判断翻译。");
  await pause(page, 3400);
  action("inspected_syn_0003_full_result", { capability_chain: "C02", stages: ["transcript", "machine_translation"] });

  await page.locator("#filter-form").scrollIntoViewIfNeeded();
  await page.locator("#filter-q").fill("SYN-0005");
  await page.locator("#filter-form button[type='submit']").click();
  await page.locator(".result-item[data-sample-id='SYN-0005']").waitFor();
  await page.locator(".result-item[data-sample-id='SYN-0005']").click();
  await page.locator("#detail-sample-id").getByText("SYN-0005", { exact: true }).waitFor();
  await overlay(page, "步骤 6 · 查看 C03 / SYN-0005 交互链路", "核对语音转写、意图、槽位、动作、回复五类字段，以及四个阶段指标：intent、slot、action、response。");
  await page.locator("#fields-heading").scrollIntoViewIfNeeded();
  for (const label of ["参考文本/标注", "系统输出", "量化指标"]) {
    await page.locator("#field-grid").getByText(label, { exact: true }).waitFor();
  }
  await pause(page, 3200);
  await page.locator("#evidence-heading").scrollIntoViewIfNeeded();
  for (const evidenceId of [
    "E-SYN-0005-STAGE-01-intent_understanding",
    "E-SYN-0005-STAGE-02-slot_filling",
    "E-SYN-0005-STAGE-03-action_execution",
    "E-SYN-0005-STAGE-04-response_generation",
  ]) {
    await page.locator("#evidence-list").getByText(evidenceId, { exact: true }).waitFor();
  }
  await overlay(page, "步骤 6 · C03 四阶段证据闭环", "意图、槽位、动作、回复分别有参考值、系统输出、精确匹配指标和 evidence_id；证据引用闭环后再回到 C01 执行修订。");
  await pause(page, 3800);
  action("inspected_syn_0005_full_result", { capability_chain: "C03", stages: ["transcript", "intent_understanding", "slot_filling", "action_execution", "response_generation"] });

  await page.locator("#filter-form").scrollIntoViewIfNeeded();
  await page.locator("#filter-q").fill("SYN-0001");
  await page.locator("#filter-form button[type='submit']").click();
  await page.locator(".result-item[data-sample-id='SYN-0001']").waitFor();
  await page.locator(".result-item[data-sample-id='SYN-0001']").click();
  await page.locator("#detail-sample-id").getByText("SYN-0001", { exact: true }).waitFor();
  await overlay(page, "步骤 7 · 回到 C01 进行人工修订", "三条能力链已在同一连续工作台中核对；现在回到 SYN-0001 提交可追溯 Web 修订。");
  await pause(page, 2400);

  const labelMarker = "LABEL-" + experimentId;
  const labelSampleId = "SYN-0002";
  const sourceJsonPath = join(root, "data", "generated_verify3", "inputs", labelSampleId + ".json");
  await overlay(page, "步骤 7 · 打开标签来源 JSON", "先打开原始输入 JSON，核对来源子场景为“噪声/数字英文混合”；本次演示清单的顶层标签是预置待纠正值。");
  await page.goto(pathToFileURL(sourceJsonPath).href, { waitUntil: "load" });
  const sourceText = await page.evaluate(() => document.body.innerText);
  if (!sourceText.includes("噪声/数字英文混合")) {
    throw new Error("source JSON does not contain expected subscene label");
  }
  action("opened_source_json_for_label_review", {
    path: relative(root, sourceJsonPath).replaceAll("\\", "/"),
    sample_id: labelSampleId,
    source_subscene: "噪声/数字英文混合",
  });
  await pause(page, 3200);

  await page.goto("http://127.0.0.1:" + workbenchPort + "/", { waitUntil: "networkidle" });
  await page.locator("#health-badge.health-badge--ok").waitFor({ timeout: 30000 });
  await page.locator('[name="batch_id"]').fill(batchId);
  await page.locator('[name="run_id"]').fill(runId);
  await page.locator("#filter-q").fill(labelSampleId);
  await page.locator("#filter-form button[type='submit']").click();
  await page.locator(".result-item[data-sample-id='SYN-0002']").waitFor();
  await page.locator(".result-item[data-sample-id='SYN-0002']").click();
  await page.locator("#detail-sample-id").getByText("SYN-0002", { exact: true }).waitFor();
  await page.locator("#label-correction-field").selectOption("subscene_type");
  const seededLabel = await page.locator("#label-correction-value").inputValue();
  if (!seededLabel.includes("普通话口述")) {
    throw new Error("seeded label was not visible before correction");
  }
  const historyBeforeLabel = await page.locator("#revision-count").innerText();
  await page.locator("#label-correction-editor").fill("recording-automation-label-reviewer");
  await page.locator("#label-correction-value").fill("噪声/数字英文混合");
  await page.locator("#label-correction-reason").fill(
    labelMarker + " 依据已打开的 SYN-0002 输入 JSON，将清单待纠正子场景改回来源标签；原派生指标、证据和结论需重跑。",
  );
  await overlay(page, "步骤 7 · 独立标签纠正操作（录制自动化）", "来源 JSON 与清单标签不一致；通过独立标签纠正入口提交噪声/数字英文混合，系统应使原派生结果失效并保留审计。");
  const labelResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/label-corrections")
      && response.request().method() === "POST",
  );
  await page.locator("#label-correction-submit").click();
  const labelResponse = await labelResponsePromise;
  if (!labelResponse.ok()) throw new Error("label correction request failed: " + labelResponse.status());
  const labelPayload = await labelResponse.json();
  const labelEvidence = (labelPayload.result["证据片段"] || []).find(
    (item) => item && item.type === "label_correction_invalidation",
  );
  if (!labelEvidence) throw new Error("label correction invalidation evidence was not returned");
  await page.locator("#revision-history").getByText(labelMarker, { exact: false }).waitFor();
  const historyAfterLabel = await page.locator("#revision-count").innerText();
  if (historyAfterLabel === historyBeforeLabel) throw new Error("label correction history did not increase");
  const inputCardAfterLabel = page.locator(".output-card").filter({ hasText: "输入数据" }).first();
  await inputCardAfterLabel.getByText("噪声/数字英文混合", { exact: false }).waitFor();
  await overlay(page, "标签纠正已保存 · 派生结果暂不判定", "页面已刷新为噪声/数字英文混合；量化指标状态为“不判定”，证据区保留被作废字段、before/after 与重跑要求。");
  await pause(page, 3600);
  action("submitted_visible_web_label_revision", {
    sample_id: labelSampleId,
    field: "subscene_type",
    marker: labelMarker,
    before: seededLabel,
    after: "噪声/数字英文混合",
    invalidation_evidence_id: labelEvidence.evidence_id,
  });

  await page.locator("#filter-form").scrollIntoViewIfNeeded();
  await page.locator("#filter-q").fill("SYN-0001");
  await page.locator("#filter-form button[type='submit']").click();
  await page.locator(".result-item[data-sample-id='SYN-0001']").waitFor();
  await page.locator(".result-item[data-sample-id='SYN-0001']").click();
  await page.locator("#detail-sample-id").getByText("SYN-0001", { exact: true }).waitFor();
  await page.locator("#revision-heading").scrollIntoViewIfNeeded();
  const revisionMarker = `REV-${experimentId}`;
  const revisedImpact = {
    text: revisionMarker + " 录制自动化复核：SYN-0001 参考文本与系统输出一致，CER=0.000000；不影响任务完成。",
    evidence_ids: ["E-SYN-0001-METRIC"],
  };
  await overlay(page, "步骤 7 · Web 修订操作（录制自动化）", `先完成 C02/C03 代表样例核对，再通过真实表单修改 C01 影响评估；不是预写数据库。修订短码 ${revisionMarker} 将贯穿数据库、审计与整批导出。`);
  await page.locator("#revision-field").selectOption("impact_assessment");
  await overlay(page, "修订前", "字段选择为“影响评估”；文本框中显示数据库当前值，录制自动化将通过真实表单改写并保留 evidence_ids。 ");
  await pause(page, 2400);
  await page.locator("#revision-editor").fill("recording-automation-reviewer");
  await page.locator("#revision-value").fill(JSON.stringify(revisedImpact, null, 2));
  await page.locator("#revision-reason").fill(
    revisionMarker + " 录制自动化通过 Web 表单提交：依据 C01 指标证据记录影响评估。",
  );
  await pause(page, 1800);
  await page.locator("#revision-submit").click();
  await page.locator("#revision-heading").scrollIntoViewIfNeeded();
  await page.locator("#revision-history").getByText(revisionMarker, { exact: false }).first().waitFor();
  await overlay(page, "保存成功 · 修订审计已写入", "真实 Web 表单响应成功；查看 before → after、修订人、时间和原因。 ");
  await pause(page, 3800);
  const impactCard = page.locator(".output-card").filter({ hasText: "影响评估" }).first();
  await impactCard.scrollIntoViewIfNeeded();
  await impactCard.getByText(revisionMarker, { exact: false }).waitFor();
  await overlay(page, "保存后字段已刷新", `影响评估卡已显示 ${revisionMarker} 与原证据引用。`);
  await pause(page, 3200);
  action("submitted_visible_web_revision", { sample_id: "SYN-0001", field: "impact_assessment", marker: revisionMarker });

  await page.locator("#filter-form").scrollIntoViewIfNeeded();
  await page.locator("#filter-q").fill("");
  await page.locator("#filter-form button[type='submit']").click();
  await page.getByText("110 条", { exact: true }).waitFor();
  await overlay(page, "Web 操作 · 实际下载本次整批结果", "工作台长驻服务仍为 RUNNING；已保留批次与运行筛选，接下来真实下载 110 条 JSON 和 CSV。");
  await pause(page, 2200);
  const jsonPromise = page.waitForEvent("download");
  await page.locator("#download-json").click();
  journey.downloads.push(await saveDownload(await jsonPromise, "whole-run-json"));
  await pause(page, 1300);
  const csvPromise = page.waitForEvent("download");
  await page.locator("#download-csv").click();
  journey.downloads.push(await saveDownload(await csvPromise, "whole-run-csv"));
  await overlay(page, "下载完成", `JSON 与 CSV 已保存；两份均为 ${batchId} / ${runId} 的 110 条结果。`);
  await pause(page, 3200);
  action("downloaded_whole_run_json_and_csv", { downloads: journey.downloads });

  await page.goto(`http://127.0.0.1:${serverPort}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#continue").waitFor();
  await pause(page, 1800);
  await page.locator("#continue").click();
  action("clicked_continue_after_visible_revision");
  await page.locator('body[data-status="complete"]').waitFor({ timeout: 180000 });
  const closeStep = page.locator(".step.completed").filter({ hasText: "正常关闭本次工作台长驻服务" }).first();
  await closeStep.waitFor();
  await closeStep.getByText("退出码 0", { exact: false }).waitFor();
  await page.locator("#pass").scrollIntoViewIfNeeded();
  await overlay(page, "步骤 13 · 工作台正常关闭", "工作台为长驻服务，启动阶段 RUNNING 属于预期；现已写入专属停止信号并 shutdown/wait，原始进程与步骤退出码均为 0。");
  await pause(page, 4600);
  action("fixed_cli_chain_completed", { step_count: 13, acceptance_ok: true, workbench_exit_code: 0, multistage_audit_ok: true });

  const reportPath = join(runDir, "evaluation-summary.html");
  const downloadedJson = join(root, journey.downloads.find((item) => item.label === "whole-run-json").path);
  await page.goto(pathToFileURL(downloadedJson).href, { waitUntil: "load" });
  const revisionMarkerFound = await page.evaluate((marker) => window.find(marker), revisionMarker);
  const labelMarkerFound = await page.evaluate((marker) => window.find(marker), labelMarker);
  if (!revisionMarkerFound) throw new Error("revision marker not found in downloaded JSON: " + revisionMarker);
  if (!labelMarkerFound) throw new Error("label marker not found in downloaded JSON: " + labelMarker);
  await overlay(page, "步骤 14 · 打开下载 JSON 并定位两类修订短码", revisionMarker + " 与 " + labelMarker + " 均已在 110 条整批下载中定位，证明 Web 修订和标签纠正进入正式导出。");
  await pause(page, 4200);
  action("opened_download_and_found_revision_markers", { revision_marker: revisionMarker, label_marker: labelMarker });

  await page.goto(pathToFileURL(reportPath).href, { waitUntil: "load" });
  await overlay(page, "步骤 15 · 打开本次现场生成的 HTML 报告", experimentId + " · 同一 batch/run · 110 条执行记录 · 109 条当前有效指标 · SYN-0002 待新 run 重算 · C02/C03 阶段指标已汇总");
  await pause(page, 5200);
  action("opened_current_run_html_report", { report: relative(root, reportPath) });
  await page.goto(`http://127.0.0.1:${serverPort}/`, { waitUntil: "domcontentloaded" });
  await page.locator('body[data-status="complete"]').waitFor();
  await page.getByText("artifact-sha256.json", { exact: false }).waitFor({ timeout: 30000 });
  await page.locator("#pass").scrollIntoViewIfNeeded();
  await overlay(page, "步骤 16 · 最终闭环", "同一实验 " + experimentId + "：命令、DB、C01/C02/C03 阶段审计、AI、两类修订、导出、报告、验收和产物哈希已闭环；标签纠正样例保留为待新 run 重算。");
  await pause(page, 5200);
  action("returned_to_final_control_page_with_hashes");
  journey.recording_finished_at = new Date().toISOString();

  await writeFile(journeyPath, JSON.stringify(journey, null, 2), "utf8");
} finally {
  if (context) await context.close();

  if (browser) await browser.close();
  try {
    await fetch(`http://127.0.0.1:${serverPort}/shutdown`, { method: "POST" });
  } catch (_error) {
    server.kill();
  }
}

const videos = (await readdir(videoTmp)).filter((name) => name.endsWith(".webm"));
if (!videos.length) throw new Error("no WebM recording was created");
const ranked = await Promise.all(videos.map(async (name) => ({ name, size: (await stat(join(videoTmp, name))).size })));
ranked.sort((left, right) => right.size - left.size);
await rename(join(videoTmp, ranked[0].name), finalVideo);

const videoStat = await stat(finalVideo);
journey.video = {
  path: relative(root, finalVideo).replaceAll("\\", "/"),
  size: videoStat.size,
  sha256: await hashFile(finalVideo),
};
await writeFile(journeyPath, JSON.stringify(journey, null, 2), "utf8");

const transcriptPath = join(runDir, "experiment-transcript.json");
const summaryPath = join(runDir, "experiment-summary.json");
const hashPath = join(runDir, "artifact-sha256.json");
const transcript = JSON.parse(await readFile(transcriptPath, "utf8"));
transcript.browser_journey = journey;
transcript.phase = "complete";
transcript.progress = 100;
transcript.artifacts_ready = true;
transcript.hash_manifest = relative(root, hashPath).replaceAll("\\", "/");
await writeFile(transcriptPath, JSON.stringify(transcript, null, 2), "utf8");
const summary = JSON.parse(await readFile(summaryPath, "utf8"));
summary.recording = journey.video;
summary.browser_downloads = journey.downloads;
summary.single_continuous_take = true;
await writeFile(summaryPath, JSON.stringify(summary, null, 2), "utf8");

const hashManifest = JSON.parse(await readFile(hashPath, "utf8"));
hashManifest.files = Object.fromEntries(
  Object.entries(hashManifest.files || {}).map(([path, digest]) => [path.replaceAll("\\", "/"), digest]),
);
const extraPaths = [finalVideo, journeyPath, transcriptPath, summaryPath, ...journey.downloads.map((item) => join(root, item.path))];
for (const path of extraPaths) {
  hashManifest.files[relative(root, path).replaceAll("\\", "/")] = await hashFile(path);
}
hashManifest.note = "artifact-sha256.json intentionally excludes its own digest";
await writeFile(hashPath, JSON.stringify(hashManifest, null, 2), "utf8");

console.log(JSON.stringify({
  ok: true, experiment_id: experimentId, batch_id: batchId, run_id: runId,
  video: journey.video, downloads: journey.downloads,
  run_dir: relative(root, runDir).replaceAll("\\", "/"),
}, null, 2));
