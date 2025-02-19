#!/usr/bin/env python
# -*- coding: utf-8 -*-

import uuid
import json
import time
import logging
import requests
from flask import Flask, request, Response, jsonify

app = Flask(__name__)
app.config["DEBUG"] = False  # 调试模式

# 输出日志到控制台
logging.basicConfig(level=logging.ERROR)
logger = app.logger

# 允许的 API key 列表，请根据实际情况修改
ALLOWED_SK_KEYS = {
    "sk-your-key-1",
    "sk-your-key-2",
    # …
}

# 模型与腾讯云 bot_app_key 的映射
MODEL_APPKEY_MAP = {
    "your-model-1": "app_key_1",
    "your-model-2": "app_key_2"
}

# 腾讯云 SSE 接口地址
API_URL = "https://wss.lke.cloud.tencent.com/v1/qbot/chat/sse"


def process_event_lines(event_lines):
    """
    处理一个 SSE 事件块：
      - 提取 event 字段和 data 字段，
      - 将多个以 "data:" 开头的行拼接成完整 JSON 字符串后解析为对象。
    """
    event_type = None
    data_lines = []
    for line in event_lines:
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):])
    if not data_lines:
        raise ValueError("没有找到 data 数据行")
    data_str = "".join(data_lines).strip()
    logger.debug("处理 SSE 数据块：事件类型[%s] 数据内容：%s", event_type, data_str)
    event_json = json.loads(data_str)
    return event_type, event_json


def fix_text(text):
    """
    修正因错误编码导致的乱码（例如 "ä½ å¥½" 应该为 "你好"）。
    如果无法用 latin1 编码则直接返回原始文本。
    """
    try:
        corrected = text.encode('latin1').decode('utf8')
        return corrected
    except Exception:
        return text


def stream_from_tencent(payload, model):
    """
    调用腾讯云接口后，将 SSE 流转化为 OpenAI 流式响应：
      - thought 事件保持流式实时输出推理过程（delta 字段 "reasoning_content"）；
      - reply 事件不进行增量输出，而是只记录最后一个 reply 块，
        等 SSE 流结束后直接一次性发送这个最后的 reply 块（delta 字段 "content"）。
      注意：仍跳过第一个 reply（仅复读用户输入）。
    """
    logger.debug("开始发起请求到腾讯云接口, payload: %s", json.dumps(payload, ensure_ascii=False))
    try:
        r = requests.post(API_URL, json=payload, stream=True, timeout=60)
        logger.debug("腾讯云接口返回状态码: %s", r.status_code)
    except Exception as e:
        logger.exception("调用腾讯云接口异常")
        yield "data: " + json.dumps({"error": str(e)}, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"
        return

    r.encoding = 'utf-8'
    event_buffer = []
    last_reasoning = ""
    final_reply = None  # 用于保存最后一个 reply 块
    first_reply = True  # 跳过第一个 reply（复读用户输入）

    for line in r.iter_lines(decode_unicode=True):
        if line:
            event_buffer.append(line)
            logger.debug("收到 SSE 行数据: %s", line)
        else:
            if event_buffer:
                try:
                    event_type, event_json = process_event_lines(event_buffer)
                    if event_type == "thought":
                        new_reasoning = event_json["payload"]["procedures"][-1]["debugging"]["content"]
                        new_reasoning = fix_text(new_reasoning)
                        if new_reasoning.startswith(last_reasoning):
                            incremental = new_reasoning[len(last_reasoning):]
                        else:
                            incremental = new_reasoning
                        last_reasoning = new_reasoning
                        if incremental.strip():
                            # 构造 chunk，delta 中用 "reasoning_content" 表示推理内容
                            chunk = {
                                "id": "chatcmpl-" + str(uuid.uuid4()),
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model,
                                "choices": [{
                                    "delta": {"reasoning_content": incremental},
                                    "index": 0,
                                    "finish_reason": None
                                }]
                            }
                            logger.debug("发送 thought chunk: %s", json.dumps(chunk, ensure_ascii=False))
                            yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                    elif event_type == "reply":
                        reply_content = fix_text(event_json["payload"]["content"])
                        if first_reply:
                            first_reply = False
                            logger.debug("跳过第一个 reply（复读用户输入）：%s", reply_content)
                        else:
                            # 每次 reply 都直接记录，最终只使用最后一次 reply 块内容
                            final_reply = reply_content
                except Exception as e:
                    logger.exception("解析 SSE 数据块异常")
                    yield "data: " + json.dumps({"error": str(e)}, ensure_ascii=False) + "\n\n"
                event_buffer = []

    # SSE 流结束后，如果存在 reply 块则一次性发送最后一次 reply 块
    if final_reply is not None and final_reply.strip():
        final_chunk = {
            "id": "chatcmpl-" + str(uuid.uuid4()),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "delta": {"content": final_reply},
                "index": 0,
                "finish_reason": "stop"
            }]
        }
        logger.debug("发送最终 reply chunk: %s", json.dumps(final_chunk, ensure_ascii=False))
        yield "data: " + json.dumps(final_chunk, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


def full_response_from_tencent(payload, model):
    """
    非流模式下，调用腾讯云接口后累积所有 SSE 数据块，
    将推理链（thought）和最终回复（reply）分别累计在
    "reasoning_content" 和 "content" 字段中。
    同样跳过第一个 reply（复读用户输入）。
    """
    logger.debug("非流模式，发送 payload: %s", json.dumps(payload, ensure_ascii=False))
    try:
        r = requests.post(API_URL, json=payload, stream=True, timeout=60)
        logger.debug("腾讯云接口返回状态码: %s", r.status_code)
    except Exception as e:
        logger.exception("调用腾讯云接口异常")
        return {"error": str(e)}

    r.encoding = 'utf-8'
    event_buffer = []
    last_reasoning = ""
    reasoning_content = ""
    final_reply = None
    first_reply = True

    for line in r.iter_lines(decode_unicode=True):
        if line:
            event_buffer.append(line)
            logger.debug("收到 SSE 数据行: %s", line)
        else:
            if event_buffer:
                try:
                    event_type, event_json = process_event_lines(event_buffer)
                    if event_type == "thought":
                        new_reasoning = event_json["payload"]["procedures"][-1]["debugging"]["content"]
                        new_reasoning = fix_text(new_reasoning)
                        if new_reasoning.startswith(last_reasoning):
                            incremental = new_reasoning[len(last_reasoning):]
                        else:
                            incremental = new_reasoning
                        last_reasoning = new_reasoning
                        reasoning_content += incremental
                    elif event_type == "reply":
                        reply_content = fix_text(event_json["payload"]["content"])
                        if first_reply:
                            first_reply = False
                        else:
                            final_reply = reply_content
                except Exception as e:
                    logger.exception("解析 SSE 数据块异常")
                    final_reply = "错误: " + str(e)
                event_buffer = []

    openai_response = {
        "id": "chatcmpl-" + str(uuid.uuid4()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "message": {
                "role": "assistant",
                "reasoning_content": reasoning_content,
                "content": final_reply or ""
            },
            "finish_reason": "stop",
            "index": 0
        }]
    }
    logger.debug("构造完整回复: %s", json.dumps(openai_response, ensure_ascii=False))
    return openai_response


@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """
    模拟 OpenAI 的 chat completions 接口：
      1. 校验 Authorization 请求头（Bearer 开头）；
      2. 从请求体中解析 model、messages 等字段；
      3. 构造调用腾讯云接口的 payload；
      4. 若 stream 为 true，则以 SSE 流方式返回数据；否则返回完整回复。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("缺少 Authorization 字段")
        return jsonify({"error": "Unauthorized: missing Authorization header"}), 401

    provided_key = auth.split(" ", 1)[1].strip()
    if provided_key not in ALLOWED_SK_KEYS:
        logger.warning("API key 未授权: %s", provided_key)
        return jsonify({"error": "Unauthorized: API key not allowed"}), 401

    data = request.get_json()
    if not data:
        logger.warning("请求体缺少 JSON 数据")
        return jsonify({"error": "Bad Request: JSON body missing"}), 400

    model = data.get("model", "")
    if model not in MODEL_APPKEY_MAP:
        logger.warning("不支持的模型: %s", model)
        return jsonify({"error": f"Invalid model. Supported: {list(MODEL_APPKEY_MAP.keys())}"}), 400

    messages = data.get("messages", [])
    if not messages:
        logger.warning("请求中缺少 messages 字段")
        return jsonify({"error": "Bad Request: 'messages' field is required"}), 400

    # 从 messages 中取最后一个 role 为 "user" 的消息作为用户输入
    user_content = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_content = msg.get("content")
            break
    if not user_content:
        logger.warning("messages 中未找到 user 消息")
        return jsonify({"error": "No user message found in 'messages'"}), 400

    logger.info("收到请求: model=%s, user_content=%s", model, user_content)

    # 支持外部传入 session_id，否则自动生成一个
    session_id = data.get("session_id", str(uuid.uuid4()))
    app_key = MODEL_APPKEY_MAP[model]
    payload = {
        "bot_app_key": app_key,
        "visitor_biz_id": "cli_user",
        "session_id": session_id,
        "request_id": str(uuid.uuid4()),
        "content": user_content,
        "visitor_labels": []
    }
    logger.debug("构造给腾讯云接口的 payload: %s", json.dumps(payload, ensure_ascii=False))

    stream_flag = data.get("stream", False)
    if stream_flag:
        return Response(stream_from_tencent(payload, model), mimetype="text/event-stream")
    else:
        response_payload = full_response_from_tencent(payload, model)
        return jsonify(response_payload)


if __name__ == '__main__':
    # 本地测试时监听 0.0.0.0:8000；生产环境请根据实际情况调整
    app.run(host="0.0.0.0", port=8000, threaded=True)
