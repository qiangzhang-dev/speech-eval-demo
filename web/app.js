"use strict";

const state = {
  selectedSampleId: null,
  selectedRunId: null,
  selectedResult: null,
};

const $ = (selector) => document.querySelector(selector);

function stringify(value) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    throw new Error(payload?.error?.message || `请求失败（${response.status}）`);
  }
  return payload;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function conclusionLevel(result) {
  const conclusion = result?.["最终结论"];
  if (conclusion && typeof conclusion === "object") return conclusion.level || "-";
  return conclusion || "-";
}

function applyConclusionClass(node, level) {
  node.className = "conclusion-badge";
  if (level === "通过") node.classList.add("conclusion-badge--pass");
  if (level === "需关注") node.classList.add("conclusion-badge--attention");
  if (level === "失败") node.classList.add("conclusion-badge--fail");
  if (level === "证据不足") node.classList.add("conclusion-badge--insufficient");
}

async function loadHealth() {
  const badge = $("#health-badge");
  try {
    await api("/api/health");
    badge.classList.remove("health-badge--error");
    badge.classList.add("health-badge--ok");
    badge.lastChild.textContent = " 本地服务正常";
  } catch (error) {
    badge.classList.remove("health-badge--ok");
    badge.classList.add("health-badge--error");
    badge.lastChild.textContent = ` ${error.message}`;
  }
}

function statCard(label, value, hint) {
  const card = element("article", "stat-card");
  card.append(
    element("span", "stat-card__label", label),
    element("strong", "stat-card__value", String(value)),
    element("span", "stat-card__hint", hint),
  );
  return card;
}

function distributionRow(label, values) {
  const row = element("div", "distribution-row");
  row.append(element("span", "distribution-label", label));
  const entries = Object.entries(values || {});
  if (!entries.length) {
    row.append(element("span", "mini-pill", "暂无数据"));
  } else {
    entries.forEach(([name, count]) => row.append(element("span", "mini-pill", `${name || "未标注"} · ${count}`)));
  }
  return row;
}

async function loadStats() {
  const grid = $("#stats-grid");
  const distribution = $("#distribution-panel");
  try {
    const stats = await api("/api/stats");
    grid.replaceChildren(
      statCard("评测结果", stats.total_results, "数据库内全部结果"),
      statCard("评测批次", stats.total_batches, `${stats.total_runs} 次运行`),
      statCard("已完成", stats.status_counts?.COMPLETED || 0, "COMPLETED 状态"),
      statCard("人工修订", stats.total_revisions, "字段级审计记录"),
      statCard("处理日志", stats.total_logs, "可追溯事件"),
    );
    distribution.replaceChildren(
      distributionRow("状态分布", stats.status_counts),
      distributionRow("场景分布", stats.scene_counts),
      distributionRow("批次分布", stats.batch_counts),
    );
    distribution.hidden = false;
  } catch (error) {
    grid.replaceChildren(element("article", "stat-card stat-card--loading", error.message));
    distribution.hidden = true;
  }
}

function filterQuery() {
  const params = new URLSearchParams({ limit: "200" });
  const fields = ["q", "status", "conclusion", "scene_type", "batch_id", "run_id"];
  fields.forEach((name) => {
    const value = $(`[name="${name}"]`).value.trim();
    if (value) params.set(name, value);
  });
  return params;
}

function showResultMessage(message, isError = false) {
  const node = $("#result-message");
  node.hidden = !message;
  node.textContent = message || "";
  node.className = `inline-message${isError ? " inline-message--error" : ""}`;
}

function renderResultList(items, total) {
  const list = $("#result-list");
  $("#result-count").textContent = `${total} 条`;
  list.replaceChildren();
  if (!items.length) {
    list.append(element("p", "muted-empty", "当前筛选条件下没有结果。"));
    return;
  }
  items.forEach((result) => {
    const sampleId = result["样例ID"];
    const level = conclusionLevel(result);
    const button = element("button", "result-item");
    button.type = "button";
    button.dataset.sampleId = sampleId;
    if (state.selectedSampleId === sampleId) button.classList.add("result-item--selected");
    const top = element("span", "result-item__top");
    top.append(element("span", "result-item__id", sampleId));
    const badge = element("span", "conclusion-badge", level);
    applyConclusionClass(badge, level);
    top.append(badge);
    button.append(top, element("span", "result-item__scene", result["场景类型"] || "未标注场景"));
    button.addEventListener("click", () => loadDetail(sampleId));
    list.append(button);
  });
}

async function loadResults() {
  showResultMessage("正在读取结果…");
  try {
    const payload = await api(`/api/results?${filterQuery().toString()}`);
    showResultMessage("");
    renderResultList(payload.items, payload.total);
  } catch (error) {
    showResultMessage(error.message, true);
    renderResultList([], 0);
  }
}

function downloadResults(format) {
  const params = filterQuery();
  params.delete("limit");
  params.set("format", format);
  window.location.assign(`/api/export?${params.toString()}`);
}

function renderMetadata(metadata) {
  const labels = {
    batch_id: "批次 ID",
    run_id: "运行 ID",
    status: "运行状态",
    created_at: "创建时间",
    metric_version: "指标版本",
    prompt_version: "诊断版本",
    subscene_type: "子场景",
  };
  const grid = $("#metadata-grid");
  grid.replaceChildren();
  Object.entries(labels).forEach(([key, label]) => {
    const wrapper = element("div", "metadata-item");
    const dt = element("dt", "", label);
    const dd = element("dd", "", metadata[key] || "-");
    wrapper.append(dt, dd);
    grid.append(wrapper);
  });
}

function renderFields(result) {
  const omitted = new Set(["样例ID", "场景类型", "任务类型", "证据片段"]);
  const wide = new Set(["质量诊断", "影响评估", "优化建议", "人工修订", "最终结论"]);
  const grid = $("#field-grid");
  grid.replaceChildren();
  Object.entries(result).forEach(([label, value]) => {
    if (omitted.has(label)) return;
    const card = element("article", `output-card${wide.has(label) ? " output-card--wide" : ""}`);
    card.append(element("h4", "", label), element("pre", "", stringify(value)));
    grid.append(card);
  });
}

function renderEvidence(evidence) {
  const list = $("#evidence-list");
  list.replaceChildren();
  const items = Array.isArray(evidence) ? evidence : [];
  $("#evidence-count").textContent = `${items.length} 条`;
  if (!items.length) {
    list.append(element("p", "muted-empty", "该结果暂无结构化证据片段。"));
    return;
  }
  items.forEach((item, index) => {
    const card = element("article", "evidence-card");
    const meta = element("div", "evidence-card__meta");
    meta.append(
      element("strong", "", item.evidence_id || `证据 ${index + 1}`),
      element("span", "", item.type || item.evidence_type || "未标注类型"),
      element("span", "", item.source || "未标注来源"),
      element("span", "", item.location || "未标注位置"),
    );
    card.append(meta, element("pre", "", stringify(item.content ?? item)));
    list.append(card);
  });
}

function renderLogs(logs) {
  const list = $("#log-list");
  list.replaceChildren();
  $("#log-count").textContent = `${logs.length} 条`;
  if (!logs.length) {
    list.append(element("p", "muted-empty", "该样例暂无处理日志。"));
    return;
  }
  logs.forEach((log) => {
    const row = element("article", "log-entry");
    row.append(
      element("div", "log-entry__meta", `${log.timestamp || "-"}\n${log.level || "-"} · ${log.stage || "-"}`),
      element("div", "log-entry__message", `${log.status || "-"} · ${log.message || "-"}`),
    );
    list.append(row);
  });
}

function renderRevisions(revisions) {
  const list = $("#revision-history");
  list.replaceChildren();
  $("#revision-count").textContent = `${revisions.length} 条历史记录`;
  if (!revisions.length) {
    list.append(element("p", "muted-empty", "尚无人工修订记录。"));
    return;
  }
  revisions.forEach((revision) => {
    const row = element("article", "revision-entry");
    row.append(
      element(
        "div",
        "revision-entry__meta",
        `${revision.edited_at || "-"}\n${revision.editor || "-"} · ${revision.field_name || "-"}`,
      ),
      element(
        "div",
        "revision-entry__message",
        `${revision.reason || "未填写原因"}\n${stringify(revision.before)} → ${stringify(revision.after)}`,
      ),
    );
    list.append(row);
  });
}

function selectListItem(sampleId) {
  document.querySelectorAll(".result-item").forEach((node) => {
    node.classList.toggle("result-item--selected", node.dataset.sampleId === sampleId);
  });
}

async function loadDetail(sampleId, runId = null) {
  const placeholder = $("#detail-placeholder");
  const content = $("#detail-content");
  placeholder.hidden = false;
  content.hidden = true;
  placeholder.querySelector("h2").textContent = "正在读取样例详情…";
  placeholder.querySelector("p").textContent = sampleId;
  try {
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    const payload = await api(`/api/results/${encodeURIComponent(sampleId)}${query}`);
    state.selectedSampleId = sampleId;
    state.selectedRunId = payload.metadata.run_id;
    state.selectedResult = payload.result;
    selectListItem(sampleId);
    $("#detail-sample-id").textContent = sampleId;
    $("#detail-subtitle").textContent = `${payload.result["场景类型"] || "未标注场景"} · ${(payload.result["任务类型"] || []).join(" / ") || "未标注任务"}`;
    const level = conclusionLevel(payload.result);
    const badge = $("#detail-conclusion");
    badge.textContent = level;
    applyConclusionClass(badge, level);
    renderMetadata(payload.metadata);
    renderFields(payload.result);
    renderEvidence(payload.result["证据片段"]);
    renderLogs(payload.logs || []);
    renderRevisions(payload.revisions || []);
    updateRevisionValue();
    updateLabelCorrectionValue();
    $("#revision-message").textContent = "";
    $("#label-correction-message").textContent = "";
    placeholder.hidden = true;
    content.hidden = false;
  } catch (error) {
    placeholder.querySelector("h2").textContent = "无法读取样例详情";
    placeholder.querySelector("p").textContent = error.message;
  }
}

const fieldLabels = {
  quality_diagnosis: "质量诊断",
  impact_assessment: "影响评估",
  recommendations: "优化建议",
};

function updateRevisionValue() {
  if (!state.selectedResult) return;
  const field = $("#revision-field").value;
  $("#revision-value").value = stringify(state.selectedResult[fieldLabels[field]]);
}

function parseRevisionValue(raw) {
  const trimmed = raw.trim();
  try {
    return JSON.parse(trimmed);
  } catch (_error) {
    return trimmed;
  }
}

async function submitRevision(event) {
  event.preventDefault();
  if (!state.selectedSampleId) return;
  const button = $("#revision-submit");
  const message = $("#revision-message");
  const field = $("#revision-field").value;
  const value = parseRevisionValue($("#revision-value").value);
  const payload = {
    sample_id: state.selectedSampleId,
    run_id: state.selectedRunId,
    editor: $("#revision-editor").value.trim(),
    reason: $("#revision-reason").value.trim(),
    changes: { [field]: value },
  };
  button.disabled = true;
  message.className = "form-message";
  message.textContent = "正在提交修订…";
  try {
    const response = await api("/api/revisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    message.className = "form-message form-message--success";
    message.textContent = response.revision_count ? `已记录 ${response.revision_count} 条字段修订。` : "内容未变化，无需新增修订记录。";
    $("#revision-reason").value = "";
    await Promise.all([loadStats(), loadDetail(state.selectedSampleId, state.selectedRunId)]);
  } catch (error) {
    message.className = "form-message form-message--error";
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

const labelCorrectionLabels = {
  scene_type: "场景类型",
  subscene_type: "子场景",
  task_types: "任务类型",
};

function updateLabelCorrectionValue() {
  if (!state.selectedResult) return;
  const field = $("#label-correction-field").value;
  const value = field === "subscene_type"
    ? state.selectedResult["输入数据"]?.subscene_type
    : state.selectedResult[labelCorrectionLabels[field]];
  $("#label-correction-value").value = field === "task_types"
    ? stringify(value || [])
    : stringify(value || "");
}

async function submitLabelCorrection(event) {
  event.preventDefault();
  if (!state.selectedSampleId) return;
  const button = $("#label-correction-submit");
  const message = $("#label-correction-message");
  const field = $("#label-correction-field").value;
  const value = parseRevisionValue($("#label-correction-value").value);
  const payload = {
    sample_id: state.selectedSampleId,
    run_id: state.selectedRunId,
    editor: $("#label-correction-editor").value.trim(),
    reason: $("#label-correction-reason").value.trim(),
    changes: { [field]: value },
  };
  button.disabled = true;
  message.className = "form-message";
  message.textContent = "正在提交标签纠正…";
  try {
    const response = await api("/api/label-corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    message.className = "form-message form-message--success";
    message.textContent = response.derived_invalidated
      ? "已记录 " + response.revision_count + " 条标签纠正，原派生结果已作废。"
      : "标签未变化，无需新增纠正记录。";
    $("#label-correction-reason").value = "";
    await Promise.all([loadStats(), loadDetail(state.selectedSampleId, state.selectedRunId)]);
  } catch (error) {
    message.className = "form-message form-message--error";
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function refreshAll() {
  await Promise.all([loadHealth(), loadStats(), loadResults()]);
  if (state.selectedSampleId) await loadDetail(state.selectedSampleId, state.selectedRunId);
}

document.addEventListener("DOMContentLoaded", () => {
  $("#filter-form").addEventListener("submit", (event) => {
    event.preventDefault();
    loadResults();
  });
  $("#clear-filter").addEventListener("click", () => {
    $("#filter-form").reset();
    loadResults();
  });
  $("#download-json").addEventListener("click", () => downloadResults("json"));
  $("#download-csv").addEventListener("click", () => downloadResults("csv"));
  $("#refresh-button").addEventListener("click", refreshAll);
  $("#revision-field").addEventListener("change", updateRevisionValue);
  $("#revision-form").addEventListener("submit", submitRevision);
  $("#label-correction-field").addEventListener("change", updateLabelCorrectionValue);
  $("#label-correction-form").addEventListener("submit", submitLabelCorrection);
  refreshAll();
});
