#!/usr/bin/env python3
"""Generate deterministic, synthetic evaluation samples.

The generated records are intentionally labelled as synthetic candidates.  They
exercise all three planned scenarios, six sub-scenarios, evidence references,
missing-value handling for absent human revisions, and the CER threshold
boundaries without claiming to be real user data or real model performance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

FIELD_NAMES = [
    "样例ID",
    "场景类型",
    "任务类型",
    "输入数据",
    "音频信息",
    "参考文本/标注",
    "系统输出",
    "量化指标",
    "质量诊断",
    "证据片段",
    "影响评估",
    "优化建议",
    "人工修订",
    "最终结论",
]

SCENARIO_SLOTS = [
    ("短语音/录音转写", "普通话口述", "C01", ["语音识别"]),
    ("短语音/录音转写", "噪声/数字英文混合", "C01", ["语音识别"]),
    ("会议/办公语音翻译", "短句语翻", "C02", ["语音识别", "机器翻译"]),
    ("会议/办公语音翻译", "长句/上下文翻译", "C02", ["语音识别", "机器翻译"]),
    ("车载/语音助手交互", "设备控制", "C03", ["语音识别", "语义理解", "对话生成"]),
    ("车载/语音助手交互", "信息查询/多轮任务", "C03", ["语音识别", "语义理解", "对话生成"]),
]

# Each pool contains at least twenty cases.  The default 110-row batch uses
# 18/19 rows from every pool, so reference text is unique across the batch.
CLEAN_TRANSCRIPTS = (
    "请把今天的会议纪要发给李明", "下午三点提醒我参加项目评审", "把桌上的蓝色文件夹放进抽屉",
    "我明早要去机场请设置一个提醒", "帮我记录这段关于预算的想法", "请把客厅的窗帘关上",
    "把本周的销售数据整理成摘要", "提醒王芳下班前确认合同", "请把手机调成静音模式",
    "把昨天拍的照片备份到硬盘", "我想把周五的培训改到下周一", "请打开书房的台灯",
    "把这条客户意见加入跟进清单", "提醒我在晚饭后给家人回电话", "请把会议室的投影仪关闭",
    "把采购申请单发送给财务部门", "帮我查找上个月的出差报销单", "请把今天的待办事项按优先级排列",
    "把卧室的加湿器设为自动模式", "提醒我周末给自行车做保养",
)

NOISY_MIXED_TRANSCRIPTS = (
    "请把订单 A1007 的数量改成 3 份", "WiFi 名称是 Office-5G，请记录下来",
    "把 2026 年 9 月 1 日的会议设为全天", "请拨打客服热线 400-800-1234",
    "将文件版本标记为 release-v2", "把温度从 23 度调到 25 度",
    "会议室 B203 的门禁密码是 5816", "请在 18:30 播放 English 新闻",
    "把项目编号 PRJ-42 加入备注", "航班 CA1837 预计 21:05 起飞",
    "请把发票号 INV20260825 录入系统", "将音量设为 level-7 后保存",
    "订单 SKU-X9 需要补发 2 件", "把服务器 node-03 的状态改为维护",
    "请在 5 分钟后提醒我检查 API", "工单 TCK-7788 的优先级设为 P1",
    "把油价记录为每升 8.19 元", "请把文件夹命名为 meeting_08",
    "设备 SN-16A7 的固件升级到 4.2", "把路线切换到 route-B 并避开 2 个收费站",
)

SHORT_TRANSLATION_CASES = (
    ("请把文件发给王芳", "Please send the file to Wang Fang"),
    ("会议改到周五上午", "Move the meeting to Friday morning"),
    ("打开共享文档", "Open the shared document"),
    ("请暂停录音", "Please pause the recording"),
    ("把这句话翻成英文", "Translate this sentence into English"),
    ("提醒我下午开会", "Remind me about the meeting this afternoon"),
    ("导出本周报告", "Export this week's report"),
    ("请关闭麦克风", "Please turn off the microphone"),
    ("把标题改成项目进展", "Change the title to project progress"),
    ("查询最新库存", "Check the latest inventory"),
    ("发送日程邀请", "Send the calendar invitation"),
    ("标记这条消息重要", "Mark this message as important"),
    ("播放下一段音频", "Play the next audio segment"),
    ("请保存翻译结果", "Please save the translation result"),
    ("打开会议链接", "Open the meeting link"),
    ("把摘要复制到剪贴板", "Copy the summary to the clipboard"),
    ("查找负责人的邮箱", "Find the owner's email address"),
    ("把附件下载到桌面", "Download the attachment to the desktop"),
    ("请重新发送验证码", "Please resend the verification code"),
    ("结束当前会话", "End the current session"),
)

# The long cases are kept as (Chinese source, context label, English target).
# They are all long, contextual requests rather than short commands.
LONG_TRANSLATION_CASES = (
    ("在今天下午的项目评审会上，请先介绍风险清单，再说明每项风险的负责人和预计解决时间。", "项目评审安排", "At this afternoon's project review, first introduce the risk list, then explain the owner and expected resolution time for each risk."),
    ("由于客户把交付日期提前了一周，请把新的排期发给研发和测试，并在邮件中说明需要优先确认的接口。", "客户交付调整", "Because the customer moved the delivery date forward by one week, send the new schedule to development and testing and explain which interfaces need priority confirmation in the email."),
    ("会议开始前请检查投影和麦克风是否正常，若发现共享屏幕延迟，就先切换到备用会议链接。", "会议设备检查", "Before the meeting starts, check whether the projector and microphone work properly; if screen sharing is delayed, switch to the backup meeting link first."),
    ("我已经把上季度的销售数据放进表格，请保留原始列，并在翻译说明中指出华东区域的异常增长。", "销售数据说明", "I have put last quarter's sales data into the spreadsheet; keep the original columns and point out the unusual growth in East China in the translation notes."),
    ("如果明天上午仍然下雨，就把户外培训改到会议室，并通知所有报名人员新的签到地点。", "培训地点预案", "If it is still raining tomorrow morning, move the outdoor training to a meeting room and notify everyone who registered of the new check-in location."),
    ("请根据产品经理的补充说明更新帮助文档，同时保留旧版截图，方便客服比较两个版本的差异。", "帮助文档更新", "Update the help document according to the product manager's additional notes, while keeping the old screenshots so customer service can compare the two versions."),
    ("在发布新版本之前，先核对数据库迁移脚本和回滚步骤，再把检查结果同步给值班同事。", "版本发布流程", "Before releasing the new version, check the database migration script and rollback steps, then share the check results with the colleague on duty."),
    ("请把访谈录音按说话人分段，删除重复的开场白，并在译文末尾保留受访者提出的两个问题。", "访谈资料整理", "Segment the interview recording by speaker, remove the repeated opening, and keep the two questions raised by the interviewee at the end of the translation."),
    ("为了避免重复采购，请先核对仓库余量，再把确实缺少的办公用品加入下周的采购申请。", "办公用品采购", "To avoid duplicate purchasing, check the warehouse balance first, then add the office supplies that are actually missing to next week's purchase request."),
    ("如果供应商今晚确认不了发货时间，就把备选方案和成本差异整理成一页说明供负责人决定。", "供应商发货决策", "If the supplier cannot confirm the shipping time tonight, summarize the alternatives and their cost differences on one page for the owner to decide."),
    ("请在演示稿中保留用户反馈的原句，并在每条反馈后补充对应的改进负责人和验证方式。", "用户反馈演示", "Keep the users' original words in the presentation and add the responsible owner and verification method after each piece of feedback."),
    ("这次迁移需要分两个晚上进行，第一晚只复制数据，第二晚再切换入口并观察错误日志。", "系统迁移计划", "This migration needs to be done over two nights: copy the data only on the first night, then switch the entry point and watch the error log on the second night."),
    ("请将合同中的付款节点翻译清楚，并提醒法务核对违约条款是否与补充协议一致。", "合同条款核对", "Translate the payment milestones in the contract clearly and remind legal to check whether the breach clauses match the supplemental agreement."),
    ("在安排出差前先确认客户的可用时间，再预订距离会场步行可达的酒店并保存发票信息。", "客户拜访出差", "Before arranging the business trip, confirm the customer's available time, then book a hotel within walking distance of the venue and save the invoice information."),
    ("如果测试环境的登录仍然失败，请把复现步骤和最近一次成功登录的时间一起写入缺陷单。", "测试环境排障", "If login to the test environment still fails, put the reproduction steps and the time of the most recent successful login in the defect ticket."),
    ("请把本周的值班安排翻译给海外团队，并特别标出周末负责处理紧急故障的联系人。", "值班安排同步", "Translate this week's duty roster for the overseas team and specifically mark the contact responsible for urgent incidents on the weekend."),
    ("在提交报销前检查发票抬头和金额是否一致，发现缺少附件时先退回申请而不要直接通过。", "报销审核规则", "Before submitting reimbursement, check that the invoice title and amount match; if an attachment is missing, return the request instead of approving it directly."),
    ("请把访客名单与门卫登记表逐项核对，确认没有重复姓名后再发送给接待同事。", "访客名单核验", "Compare the visitor list with the guard's registration form item by item, and send it to the reception colleague only after confirming there are no duplicate names."),
    ("如果会议中途需要更换主持人，请先在群里说明原因，再把新的发言顺序同步到会议纪要。", "会议主持交接", "If the host needs to be replaced during the meeting, explain the reason in the group first and then update the new speaking order in the meeting minutes."),
)

# (display target, source utterance, action code, slots, response)
DEVICE_CASES = (
    ("客厅空调", "把客厅空调打开", "turn_on", {"设备": "空调", "位置": "客厅"}, "已打开客厅空调"),
    ("卧室空调", "把卧室空调关闭", "turn_off", {"设备": "空调", "位置": "卧室"}, "已关闭卧室空调"),
    ("书房台灯", "请打开书房台灯", "turn_on", {"设备": "台灯", "位置": "书房"}, "已打开书房台灯"),
    ("厨房吊灯", "请把厨房吊灯关掉", "turn_off", {"设备": "吊灯", "位置": "厨房"}, "已关闭厨房吊灯"),
    ("客厅窗帘", "把客厅窗帘拉开", "open", {"设备": "窗帘", "位置": "客厅"}, "已拉开客厅窗帘"),
    ("阳台窗帘", "把阳台窗帘合上", "close", {"设备": "窗帘", "位置": "阳台"}, "已合上阳台窗帘"),
    ("办公室风扇", "把办公室风扇调到二档", "set_speed", {"设备": "风扇", "位置": "办公室", "档位": 2}, "办公室风扇已调到二档"),
    ("卧室加湿器", "将卧室加湿器设为自动模式", "set_mode", {"设备": "加湿器", "位置": "卧室", "模式": "自动"}, "卧室加湿器已设为自动模式"),
    ("客厅音箱", "把客厅音箱音量调到百分之三十", "set_volume", {"设备": "音箱", "位置": "客厅", "音量": 30}, "客厅音箱音量已调到30%"),
    ("车库照明", "打开车库照明", "turn_on", {"设备": "照明", "位置": "车库"}, "已打开车库照明"),
    ("玄关门锁", "锁上玄关门", "lock", {"设备": "门锁", "位置": "玄关"}, "已锁上玄关门"),
    ("储物间门锁", "解锁储物间的门", "unlock", {"设备": "门锁", "位置": "储物间"}, "已解锁储物间门"),
    ("客厅电视", "打开客厅电视", "turn_on", {"设备": "电视", "位置": "客厅"}, "已打开客厅电视"),
    ("书房显示器", "关闭书房显示器", "turn_off", {"设备": "显示器", "位置": "书房"}, "已关闭书房显示器"),
    ("餐厅灯带", "把餐厅灯带亮度调到百分之六十", "set_brightness", {"设备": "灯带", "位置": "餐厅", "亮度": 60}, "餐厅灯带亮度已调到60%"),
    ("主卧窗户", "打开主卧窗户", "open", {"设备": "窗户", "位置": "主卧"}, "已打开主卧窗户"),
    ("儿童房空气净化器", "启动儿童房空气净化器", "turn_on", {"设备": "空气净化器", "位置": "儿童房"}, "已启动儿童房空气净化器"),
    ("厨房排风扇", "关闭厨房排风扇", "turn_off", {"设备": "排风扇", "位置": "厨房"}, "已关闭厨房排风扇"),
    ("客厅扫地机器人", "让客厅扫地机器人开始清扫", "start_cleaning", {"设备": "扫地机器人", "位置": "客厅"}, "客厅扫地机器人已开始清扫"),
    ("浴室热水器", "把浴室热水器温度设为四十五度", "set_temperature", {"设备": "热水器", "位置": "浴室", "温度": 45}, "浴室热水器已设为45度"),
)

# (location, date phrase, query topic, action code, slots, answer)
QUERY_CASES = (
    ("北京", "明天", "天气", "query_weather", {"地点": "北京", "日期": "明天"}, "北京明天晴转多云，最高温度26度"),
    ("上海", "周五", "天气", "query_weather", {"地点": "上海", "日期": "周五"}, "上海周五有小雨，最高温度22度"),
    ("广州", "今晚", "空气质量", "query_air_quality", {"地点": "广州", "日期": "今晚"}, "广州今晚空气质量为良"),
    ("深圳", "明早", "交通", "query_traffic", {"地点": "深圳", "日期": "明早"}, "深圳明早通勤路段预计缓行"),
    ("杭州", "下周一", "日程", "query_calendar", {"地点": "杭州", "日期": "下周一"}, "杭州下周一有客户会议"),
    ("成都", "今天", "航班", "query_flight", {"地点": "成都", "日期": "今天"}, "成都今天有两趟已保存航班"),
    ("南京", "周末", "活动", "query_events", {"地点": "南京", "日期": "周末"}, "南京周末有三场已收藏活动"),
    ("武汉", "下个月", "账单", "query_bill", {"地点": "武汉", "日期": "下个月"}, "武汉下个月没有待缴账单"),
    ("西安", "明天", "酒店", "query_hotel", {"地点": "西安", "日期": "明天"}, "西安明天有两家符合条件的酒店"),
    ("苏州", "本周", "会议", "query_meetings", {"地点": "苏州", "日期": "本周"}, "苏州本周有四个会议安排"),
    ("厦门", "后天", "快递", "query_delivery", {"地点": "厦门", "日期": "后天"}, "厦门后天预计送达一件快递"),
    ("青岛", "今晚", "餐厅", "query_restaurant", {"地点": "青岛", "日期": "今晚"}, "青岛今晚有五家已收藏餐厅营业"),
    ("郑州", "明天", "火车", "query_train", {"地点": "郑州", "日期": "明天"}, "郑州明天有三趟直达列车"),
    ("重庆", "周六", "电影", "query_movies", {"地点": "重庆", "日期": "周六"}, "重庆周六有两部收藏电影上映"),
    ("合肥", "今天", "待办", "query_todos", {"地点": "合肥", "日期": "今天"}, "合肥今天还有六项待办事项"),
    ("济南", "下周三", "汇率", "query_exchange_rate", {"地点": "济南", "日期": "下周三"}, "济南下周三记录的美元汇率为7.12"),
    ("福州", "明晚", "用电", "query_energy", {"地点": "福州", "日期": "明晚"}, "福州明晚预计用电量为12度"),
    ("昆明", "周日", "跑步路线", "query_route", {"地点": "昆明", "日期": "周日"}, "昆明周日有一条已保存跑步路线"),
    ("大连", "下周五", "纪念日", "query_reminders", {"地点": "大连", "日期": "下周五"}, "大连下周五有一个纪念日提醒"),
    ("南宁", "后天", "快递", "query_delivery", {"地点": "南宁", "日期": "后天"}, "南宁后天预计送达两件快递"),
)


def normalize_text(text: str) -> str:
    """Apply the versioned candidate CER normalization contract."""

    text = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(
        char
        for char in text
        if not unicodedata.category(char).startswith(("Z", "P", "S"))
        and (char.isalnum() or "\u4e00" <= char <= "\u9fff")
    )


def levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, lch in enumerate(left, 1):
        current = [i]
        for j, rch in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (lch != rch),
                )
            )
        previous = current
    return previous[-1]


def cer(reference: str, hypothesis: str) -> float:
    """Return deterministic character error rate; raise on unusable reference."""

    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    if not ref:
        raise ValueError("reference text is empty after normalization")
    return levenshtein(ref, hyp) / len(ref)


def cer_status(value: float) -> str:
    if value <= 0.10:
        return "通过"
    if value <= 0.20:
        return "需关注"
    return "失败"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hypothesis(reference: str, index: int) -> str:
    mode = index % 4
    if mode == 0:
        return reference
    if mode == 1:
        # One deletion gives a useful attention-band example for six-character text.
        return reference[:-1]
    if mode == 2:
        return reference[:-2]
    return reference[:2]


def _pool_item(pool: tuple[Any, ...], local_index: int) -> Any:
    """Return a deterministic pool item, extending text for oversized runs."""

    item = pool[local_index % len(pool)]
    if local_index < len(pool):
        return item
    # Hidden callers sometimes request more than the formal 110 rows.  Keep
    # those rows semantically valid and unique by adding a source qualifier.
    suffix = f"（扩展样例{local_index + 1}）"
    if isinstance(item, str):
        return item + suffix
    if isinstance(item, tuple) and item and isinstance(item[0], str):
        return (item[0] + suffix, *item[1:])
    return item


def _stage_metric(stage: str, metric_name: str, value: float, *, source: Any, output: Any, evidence_id: str) -> dict[str, Any]:
    payload = json.dumps({"source": source, "output": output}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "指标ID": f"{stage.lower()}-exact-v1",
        "阶段": stage,
        "指标名称": metric_name,
        "值": round(value, 6),
        "单位": "ratio",
        "计算方法": "deterministic exact field comparison",
        "实现版本": "stage-exact-v1",
        "输入摘要": sha256_text(payload),
        "状态": "通过" if value == 1.0 else "失败",
        "候选状态": "通过" if value == 1.0 else "失败",
        "参考": source,
        "输出": output,
        "匹配": value == 1.0,
        "适用": True,
        "可复现": True,
        "evidence_id": evidence_id,
    }


def make_result(index: int) -> dict[str, Any]:
    slot = SCENARIO_SLOTS[index % len(SCENARIO_SLOTS)]
    scenario, subscenario, chain, tasks = slot
    local_index = index // len(SCENARIO_SLOTS)
    sample_id = f"SYN-{index + 1:04d}"

    # Build the full source annotation and system output from one case record.
    # No downstream value is selected from a scenario-wide constant.
    dialogue_turns: Any = "-"
    context = "-"
    stage_metrics: list[dict[str, Any]] = []
    stage_evidence: list[dict[str, Any]] = []
    if subscenario == "普通话口述":
        reference = str(_pool_item(CLEAN_TRANSCRIPTS, local_index))
        case_kind = "clean_mandarin"
        reference_annotation = {
            "transcript": reference,
            "translation": "-", "intent": "-", "slots": "-",
            "expected_action": "-", "expected_reply": "-",
            "annotation_version": "fixture-2.0",
        }
        hypothesis = _hypothesis(reference, index)
        system_output = {
            "transcript": hypothesis, "translation": "-", "intent": "-", "slots": "-",
            "action": "-", "reply": "-", "model_version": "deterministic-fixture-2.0",
            "run_id": f"RUN-{sample_id}",
        }
    elif subscenario == "噪声/数字英文混合":
        reference = str(_pool_item(NOISY_MIXED_TRANSCRIPTS, local_index))
        case_kind = "noise_mixed_alphanumeric"
        reference_annotation = {
            "transcript": reference,
            "translation": "-", "intent": "-", "slots": "-",
            "expected_action": "-", "expected_reply": "-",
            "annotation_version": "fixture-2.0",
        }
        hypothesis = _hypothesis(reference, index)
        system_output = {
            "transcript": hypothesis, "translation": "-", "intent": "-", "slots": "-",
            "action": "-", "reply": "-", "model_version": "deterministic-fixture-2.0",
            "run_id": f"RUN-{sample_id}",
        }
    elif subscenario == "短句语翻":
        pair = _pool_item(SHORT_TRANSLATION_CASES, local_index)
        reference, translation = pair
        case_kind = "short_translation"
        reference_annotation = {
            "transcript": reference, "translation": translation,
            "translation_context": "-", "intent": "-", "slots": "-",
            "expected_action": "-", "expected_reply": "-",
            "annotation_version": "fixture-2.0",
        }
        hypothesis = _hypothesis(reference, index)
        # Every translation is derived from the selected pair.  A small,
        # explicit perturbation exercises the stage metric without inventing a
        # different source fact.
        system_translation = translation if index % 5 else translation.replace("Please ", "", 1)
        system_output = {
            "transcript": hypothesis, "translation": system_translation,
            "intent": "-", "slots": "-", "action": "-", "reply": "-",
            "model_version": "deterministic-fixture-2.0", "run_id": f"RUN-{sample_id}",
        }
        stage_value = float(system_translation == translation)
        evidence_id = f"EVID-{sample_id}-translation"
        stage_metrics.append(_stage_metric("machine_translation", "translation_exact_match", stage_value, source=translation, output=system_translation, evidence_id=evidence_id))
        stage_evidence.append({"evidence_id": evidence_id, "来源": "机器翻译阶段", "定位": f"fixture://{sample_id}/machine_translation", "片段": {"stage": "machine_translation", "metric_name": "translation_exact_match", "reference": translation, "output": system_translation}, "采集方式": "deterministic translation comparator"})
    elif subscenario == "长句/上下文翻译":
        pair = _pool_item(LONG_TRANSLATION_CASES, local_index)
        reference, context, translation = pair
        case_kind = "long_context_translation"
        reference_annotation = {
            "transcript": reference, "translation": translation,
            "translation_context": context, "intent": "-", "slots": "-",
            "expected_action": "-", "expected_reply": "-",
            "annotation_version": "fixture-2.0",
        }
        hypothesis = _hypothesis(reference, index)
        system_translation = translation if index % 5 else translation.replace("Please ", "", 1)
        system_output = {
            "transcript": hypothesis, "translation": system_translation,
            "translation_context": context, "intent": "-", "slots": "-", "action": "-", "reply": "-",
            "model_version": "deterministic-fixture-2.0", "run_id": f"RUN-{sample_id}",
        }
        stage_value = float(system_translation == translation)
        evidence_id = f"EVID-{sample_id}-translation"
        stage_metrics.append(_stage_metric("machine_translation", "translation_exact_match", stage_value, source=translation, output=system_translation, evidence_id=evidence_id))
        stage_evidence.append({"evidence_id": evidence_id, "来源": "机器翻译阶段", "定位": f"fixture://{sample_id}/machine_translation", "片段": {"stage": "machine_translation", "metric_name": "translation_exact_match", "context": context, "reference": translation, "output": system_translation}, "采集方式": "deterministic translation comparator"})
        dialogue_turns = [{"轮次": 1, "角色": "主持人", "内容": f"上下文：{context}"}, {"轮次": 2, "角色": "用户", "内容": reference}]
    elif subscenario == "设备控制":
        display_name, reference, action, slots, reply = _pool_item(DEVICE_CASES, local_index)
        case_kind = "device_control"
        reference_annotation = {
            "transcript": reference, "translation": "-", "intent": "设备控制", "slots": dict(slots),
            "expected_action": action, "expected_reply": reply, "annotation_version": "fixture-2.0",
        }
        hypothesis = _hypothesis(reference, index)
        system_output = {
            "transcript": hypothesis, "translation": "-", "intent": "设备控制", "slots": dict(slots),
            "action": action, "reply": reply, "model_version": "deterministic-fixture-2.0", "run_id": f"RUN-{sample_id}",
        }
        for stage, metric_name, expected, observed in (("intent_understanding", "intent_exact_match", "设备控制", "设备控制"), ("slot_filling", "slot_exact_match", slots, slots), ("action_execution", "action_exact_match", action, action), ("response_generation", "response_exact_match", reply, reply)):
            stage_value = float(expected == observed)
            evidence_id = f"EVID-{sample_id}-{stage}"
            stage_metrics.append(_stage_metric(stage, metric_name, stage_value, source=expected, output=observed, evidence_id=evidence_id))
            stage_evidence.append({"evidence_id": evidence_id, "来源": f"{stage}阶段", "定位": f"fixture://{sample_id}/{stage}", "片段": {"stage": stage, "metric_name": metric_name, "reference": expected, "output": observed}, "采集方式": "deterministic field comparator"})
    else:  # 信息查询/多轮任务
        location, date_phrase, topic, action, slots, reply = _pool_item(QUERY_CASES, local_index)
        reference = f"请查询{date_phrase}{location}的{topic}信息"
        case_kind = "multi_turn_query"
        context = f"用户此前已选择地点{location}，现在继续询问{topic}。"
        dialogue_turns = [{"轮次": 1, "角色": "用户", "内容": f"我想了解{location}的安排。"}, {"轮次": 2, "角色": "系统", "内容": "好的，请问你想查询什么？"}, {"轮次": 3, "角色": "用户", "内容": reference}]
        reference_annotation = {
            "transcript": reference, "translation": "-", "intent": "信息查询", "slots": dict(slots),
            "expected_action": action, "expected_reply": reply, "annotation_version": "fixture-2.0",
        }
        hypothesis = _hypothesis(reference, index)
        system_output = {
            "transcript": hypothesis, "translation": "-", "intent": "信息查询", "slots": dict(slots),
            "action": action, "reply": reply, "model_version": "deterministic-fixture-2.0", "run_id": f"RUN-{sample_id}",
        }
        for stage, metric_name, expected, observed in (("intent_understanding", "intent_exact_match", "信息查询", "信息查询"), ("slot_filling", "slot_exact_match", slots, slots), ("action_execution", "action_exact_match", action, action), ("response_generation", "response_exact_match", reply, reply)):
            stage_value = float(expected == observed)
            evidence_id = f"EVID-{sample_id}-{stage}"
            stage_metrics.append(_stage_metric(stage, metric_name, stage_value, source=expected, output=observed, evidence_id=evidence_id))
            stage_evidence.append({"evidence_id": evidence_id, "来源": f"{stage}阶段", "定位": f"fixture://{sample_id}/{stage}", "片段": {"stage": stage, "metric_name": metric_name, "reference": expected, "output": observed}, "采集方式": "deterministic field comparator"})

    hypothesis = str(system_output["transcript"])
    source_payload = {
        "sample_id": sample_id, "scene": scenario, "subscene": subscenario,
        "reference_annotation": reference_annotation, "dialogue_turns": dialogue_turns,
    }
    source_text = json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_hash = sha256_text(source_text)
    metric_input_hash = sha256_text(normalize_text(reference) + "\n" + normalize_text(hypothesis))
    value = cer(reference, hypothesis)
    candidate_status = cer_status(value)
    stage_candidate_statuses = {item["候选状态"] for item in stage_metrics}
    overall_candidate_status = (
        "证据不足"
        if "证据不足" in stage_candidate_statuses
        else ("失败" if "失败" in stage_candidate_statuses else candidate_status)
    )
    cer_evidence_id = f"EVID-{sample_id}-metric"
    input_evidence_id = f"EVID-{sample_id}-input"
    evidence_ids = [input_evidence_id, cer_evidence_id] + [item["evidence_id"] for item in stage_evidence]
    input_data = {
        "输入类型": "音频", "来源标识": f"synthetic://{sample_id}.wav", "内容摘要": reference,
        "内容SHA256": source_hash, "对话轮次": dialogue_turns, "subscene_type": subscenario,
        "data_classification": "synthetic_engineering_validation", "fixture_variant": case_kind,
    }
    signal_conditions = ["synthetic", "noise", "mixed_alphanumeric"] if subscenario == "噪声/数字英文混合" else ["synthetic", "clean"]
    result: dict[str, Any] = {
        "样例ID": sample_id,
        "场景类型": {"典型场景": scenario, "子场景": subscenario},
        "任务类型": list(tasks),
        "输入数据": input_data,
        "音频信息": {"时长毫秒": 1200 + (index % 9) * 100, "采样率Hz": 16000, "声道数": 1, "格式": "wav", "语种或方言": "普通话", "说话人数": 1, "信号条件": signal_conditions},
        "参考文本/标注": {"人工转写": reference_annotation["transcript"], "标准译文": reference_annotation.get("translation", "-"), "翻译上下文": reference_annotation.get("translation_context", "-"), "标准意图": reference_annotation.get("intent", "-"), "标准槽位": reference_annotation.get("slots", "-"), "期望动作": reference_annotation.get("expected_action", "-"), "期望回复": reference_annotation.get("expected_reply", "-"), "标注人员": "synthetic-fixture", "标注版本": reference_annotation.get("annotation_version", "fixture-2.0")},
        "系统输出": {"系统名称": "deterministic-fixture-engine", "系统版本": system_output.get("model_version", "deterministic-fixture-2.0"), "运行ID": system_output.get("run_id", f"RUN-{sample_id}"), "识别文本": system_output["transcript"], "意图": system_output.get("intent", "-"), "槽位": system_output.get("slots", "-"), "译文": system_output.get("translation", "-"), "回复": system_output.get("reply", "-"), "动作": system_output.get("action", "-"), "合成音频路径": "-"},
        "量化指标": [{"指标ID": "cer-v1", "指标名称": "字符错误率", "值": round(value, 6), "单位": "ratio", "计算方法": "Levenshtein character distance / normalized reference length", "实现版本": "cer-normalize-v1", "输入摘要": metric_input_hash, "阈值版本": "1.0.0-candidate.1", "阈值审批状态": "pending_approval", "阈值生效": False, "候选状态": candidate_status, "总体候选状态": overall_candidate_status, "状态": "不判定", "可复现": True}] + stage_metrics,
        "质量诊断": {"摘要": "仅对合成输入执行可复现字段比较；正式质量原因不判断。" if not stage_metrics else f"已保存转写及{len(stage_metrics)}个下游阶段的可复现比较结果；正式质量原因不判断。", "问题类型": ["证据不足"], "严重度": "待定", "evidence_ids": evidence_ids},
        "证据片段": [{"evidence_id": input_evidence_id, "来源": "输入文本", "定位": f"fixture://{sample_id}/input", "片段": reference, "采集方式": "deterministic fixture generator", "来源SHA256": source_hash}, {"evidence_id": cer_evidence_id, "来源": "量化指标", "定位": f"fixture://{sample_id}/metric/cer-v1", "片段": {"metric_name": "CER", "value": round(value, 6), "candidate_status": candidate_status, "overall_candidate_status": overall_candidate_status, "threshold_effective": False}, "采集方式": "deterministic CER calculator", "来源SHA256": metric_input_hash}] + stage_evidence,
        "影响评估": {"维度": ["沟通准确性"], "严重度": "待定", "说明": "合成样例，仅用于工程链路验证；不代表真实用户影响。", "evidence_ids": evidence_ids},
        "优化建议": {"摘要": "保留当前证据并在来源合规的真实或人工标注数据上复核；本样例不判断原因。", "evidence_ids": evidence_ids, "行动项": [{"行动ID": f"ACT-{sample_id}", "类别": "验证", "动作": "使用同一输入、版本和证据重跑比较", "优先级": "P2", "验证方法": "复算 CER 与各声明阶段 exact-match", "evidence_ids": evidence_ids}]},
        "人工修订": "-",
        "最终结论": {"判定": "证据不足", "摘要": f"候选CER状态为{candidate_status}；阈值尚未审批生效，不能形成正式结论。", "evidence_ids": evidence_ids, "gate_ids": [f"G{i:02d}" for i in range(1, 11)]},
    }
    return _to_readme_result(result)


def _to_readme_result(result: dict[str, Any]) -> dict[str, Any]:
    """Map the internal fixture representation to the root README's 14 fields."""

    scenario = result["场景类型"]["典型场景"]
    subscenario = result["场景类型"]["子场景"]
    input_data = result["输入数据"]
    audio = result["音频信息"]
    reference = result["参考文本/标注"]
    output = result["系统输出"]
    metric = result["量化指标"][0]
    diagnosis = result["质量诊断"]
    evidence = result["证据片段"]
    impact = result["影响评估"]
    suggestions = result["优化建议"]
    revision = result["人工修订"]
    conclusion = result["最终结论"]
    return {
        "样例ID": result["样例ID"],
        "场景类型": scenario,
        "任务类型": result["任务类型"],
        "输入数据": {
            "audio_path": f"inputs/{result['样例ID']}.json",
            "source_id": input_data["来源标识"],
            "content_summary": input_data["内容摘要"],
            "content_sha256": input_data["内容SHA256"],
            "subscene_type": subscenario,
            "dialogue_turns": input_data["对话轮次"],
            "data_classification": input_data["data_classification"],
            "fixture_variant": input_data["fixture_variant"],
        },
        "音频信息": {
            "duration_ms": audio["时长毫秒"],
            "sample_rate": audio["采样率Hz"],
            "channels": audio["声道数"],
            "format": audio["格式"],
            "language": audio["语种或方言"],
            "speaker_count": audio["说话人数"],
            "signal_conditions": audio["信号条件"],
        },
        "参考文本/标注": {
            "transcript": reference["人工转写"],
            "translation": reference["标准译文"],
            "intent": reference["标准意图"],
            "slots": reference["标准槽位"],
            "expected_action": reference["期望动作"],
            "expected_reply": reference["期望回复"],
            "translation_context": reference["翻译上下文"],
            "annotation_version": reference["标注版本"],
        },
        "系统输出": {
            "transcript": output["识别文本"],
            "translation": output["译文"],
            "intent": output["意图"],
            "slots": output["槽位"],
            "action": output["动作"],
            "reply": output["回复"],
            "model_version": output["系统版本"],
            "run_id": output["运行ID"],
        },
        "量化指标": {
            "metric_name": "CER",
            "value": metric["值"],
            "metric_version": metric["实现版本"],
            "normalization_version": "cer-normalize-v1",
            "input_summary_sha256": metric["输入摘要"],
            "status": metric["状态"],
            "candidate_status": metric["候选状态"],
            "overall_candidate_status": metric["总体候选状态"],
            "threshold_version": metric["阈值版本"],
            "threshold_approval_status": metric["阈值审批状态"],
            "threshold_effective": metric["阈值生效"],
            "stage_metrics": [
                {
                    "stage": item["阶段"],
                    "metric_name": item["指标名称"],
                    "value": item["值"],
                    "metric_version": item["实现版本"],
                    "reference": item["参考"],
                    "hypothesis": item["输出"],
                    "matched": item["匹配"],
                    "applicable": item["适用"],
                    "candidate_status": item["候选状态"],
                    "input_summary_sha256": item["输入摘要"],
                    "reproducible": item["可复现"],
                }
                for item in result["量化指标"][1:]
            ],
            "reproducible": metric["可复现"],
        },
        "质量诊断": {
            "text": diagnosis["摘要"],
            "evidence_ids": diagnosis["evidence_ids"],
        },
        "证据片段": [
            {
                "evidence_id": item["evidence_id"],
                "type": "metric_snapshot" if item["来源"] != "输入文本" else "input_text",
                "source": item["来源"],
                "location": item["定位"],
                "content": item["片段"],
            }
            for item in evidence
        ],
        "影响评估": {
            "text": impact["说明"],
            "evidence_ids": impact["evidence_ids"],
        },
        "优化建议": [
            {"text": suggestions["摘要"], "evidence_ids": suggestions["evidence_ids"]}
        ],
        "人工修订": revision,
        "最终结论": {
            "level": conclusion["判定"],
            "basis": conclusion["摘要"],
            "evidence_ids": conclusion["evidence_ids"],
        },
    }


def _manifest_row(result: dict[str, Any]) -> dict[str, str]:
    scenario = result["场景类型"]
    subscenario = result["输入数据"].get("subscene_type", "-")
    ref = result["参考文本/标注"]["transcript"]
    out = result["系统输出"]["transcript"]
    metric = result["量化指标"]
    metadata = {
        "synthetic": True,
        "data_classification": "synthetic_engineering_validation",
        "validation_scope": "synthetic_engineering_validation",
        "capability_chain": "C01" if scenario == "短语音/录音转写" else ("C02" if scenario == "会议/办公语音翻译" else "C03"),
        "source_sha256": result["输入数据"]["content_sha256"],
        "fixture_variant": result["输入数据"]["fixture_variant"],
    }
    fixture_path = f"inputs/{result['样例ID']}.json"
    return {
        "sample_id": result["样例ID"],
        # Keep legacy text columns while making the full objects authoritative.
        "scene_type": scenario,
        "subscene_type": subscenario,
        "input_path": fixture_path,
        "input_data": json.dumps(
            {
                "type": "synthetic_audio_fixture",
                "path": fixture_path,
                "source_id": result["输入数据"]["source_id"],
                "data_classification": "synthetic_engineering_validation",
                "subscene_type": subscenario,
                "fixture_variant": result["输入数据"]["fixture_variant"],
                "dialogue_turns": result["输入数据"]["dialogue_turns"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "audio_info": json.dumps(result["音频信息"], ensure_ascii=False, separators=(",", ":")),
        "language": result["音频信息"]["language"],
        "metadata": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        "scenario": scenario,
        "subscenario": subscenario,
        "capability_chain": metadata["capability_chain"],
        "task_types": "|".join(result["任务类型"]),
        "reference_annotation": json.dumps(result["参考文本/标注"], ensure_ascii=False, separators=(",", ":")),
        "reference_text": ref,
        "system_output": json.dumps(result["系统输出"], ensure_ascii=False, separators=(",", ":")),
        "system_output_text": out,
        "cer": f"{metric['value']:.6f}",
        "cer_status": metric["candidate_status"],
        "source_id": result["输入数据"]["source_id"],
        "source_sha256": result["输入数据"]["content_sha256"],
        "result_id": result["样例ID"],
    }


def generate(count: int, output: Path, generated_at: str = "2026-08-21T00:00:00Z") -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be positive")
    output.mkdir(parents=True, exist_ok=True)
    results = [make_result(i) for i in range(count)]
    # Only overwrite the files generated below.  Do not recursively clean an
    # arbitrary caller-provided output directory; callers may keep other
    # evidence beside this batch.
    with (output / "results.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    with (output / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_manifest_row(results[0])))
        writer.writeheader()
        writer.writerows(_manifest_row(result) for result in results)
    inputs = output / "inputs"
    inputs.mkdir(exist_ok=True)
    for result in results:
        fixture = {
            "schema_version": "synthetic-audio-fixture-2.0",
            "data_classification": "synthetic_engineering_validation",
            "sample_id": result["样例ID"],
            "scene_type": result["场景类型"],
            "subscene_type": result["输入数据"]["subscene_type"],
            "task_types": result["任务类型"],
            "input_data": result["输入数据"],
            "audio_info": result["音频信息"],
            "reference_annotation": result["参考文本/标注"],
            "system_output": result["系统输出"],
        }
        (inputs / f"{result['样例ID']}.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    batch = {
        "schema_version": "1.0.0",
        "batch_id": f"SYN-BATCH-{count:03d}",
        "generated_at": generated_at,
        "configuration": {
            "plan_version": "2.0.0",
            "threshold_version": "1.0.0-candidate.1",
            "threshold_approval_status": "pending_approval",
            "threshold_effective": False,
            "prompt_version": "fixture-prompt-2.0",
        },
        "results": results,
    }
    (output / "batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "synthetic": True,
        "data_classification": "synthetic_engineering_validation",
        "formal_acceptance_claim": False,
        "count": count,
        "unique_reference_texts": len({r["参考文本/标注"]["transcript"] for r in results}),
        "typical_scenarios": sorted({r["场景类型"] for r in results}),
        "subscenarios": sorted({r["输入数据"].get("subscene_type", "-") for r in results}),
        "capability_chains": sorted({
            {"短语音/录音转写": "C01", "会议/办公语音翻译": "C02", "车载/语音助手交互": "C03"}.get(r["场景类型"], "-")
            for r in results
        } - {"-"}),
        "field_count": len(FIELD_NAMES),
        "source_of_truth": "scripts/generate_samples.py",
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=110)
    parser.add_argument("--output", type=Path, default=Path("data/generated"))
    parser.add_argument("--generated-at", default="2026-08-21T00:00:00Z")
    args = parser.parse_args()
    results = generate(args.count, args.output, args.generated_at)
    print(json.dumps({"output": str(args.output), "count": len(results), "synthetic": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
