# -*- coding: utf-8 -*-
r"""本地大模型服务探测

挨个试常见本地推理服务的端口，报告哪个活着、有哪些模型。
只用标准库，超时很短，不会卡住。

常见端口：
  11434  Ollama
  1234   LM Studio
  8080   llama.cpp server / LocalAI
  8000   vLLM
  5000   text-generation-webui
  5001   KoboldCpp
  7860   text-generation-webui (gradio)
  11435  Ollama 备用

用法： python tools/probe_llm.py
"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TIMEOUT = 1.2

# (名字, host, port, 探测路径, 解析函数名)
TARGETS = [
    ("Ollama", "127.0.0.1", 11434, "/api/tags", "ollama"),
    ("Ollama(备用)", "127.0.0.1", 11435, "/api/tags", "ollama"),
    ("LM Studio", "127.0.0.1", 1234, "/v1/models", "openai"),
    ("llama.cpp", "127.0.0.1", 8080, "/v1/models", "openai"),
    ("LocalAI", "127.0.0.1", 8080, "/v1/models", "openai"),
    ("vLLM", "127.0.0.1", 8000, "/v1/models", "openai"),
    ("text-gen-webui", "127.0.0.1", 5000, "/v1/models", "openai"),
    ("KoboldCpp", "127.0.0.1", 5001, "/v1/models", "openai"),
    ("Jan", "127.0.0.1", 1337, "/v1/models", "openai"),
    ("GPT4All", "127.0.0.1", 4891, "/v1/models", "openai"),
    ("SGLang", "127.0.0.1", 30000, "/v1/models", "openai"),
]


def port_open(host, port):
    s = socket.socket()
    s.settimeout(0.35)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def fetch(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def parse(kind, data):
    if kind == "ollama":
        return [m.get("name") or m.get("model") for m in (data.get("models") or [])]
    out = []
    for m in (data.get("data") or data.get("models") or []):
        if isinstance(m, dict):
            out.append(m.get("id") or m.get("name"))
        else:
            out.append(str(m))
    return [x for x in out if x]


def main():
    print("=" * 74)
    print("本地大模型服务探测")
    print("=" * 74)

    found = []
    for name, host, port, path, kind in TARGETS:
        if not port_open(host, port):
            print("  %-16s %s:%-6d 端口未开" % (name, host, port))
            continue
        url = "http://%s:%d%s" % (host, port, path)
        try:
            data = fetch(url)
            models = parse(kind, data)
            print("  %-16s %s:%-6d 活着！模型 %d 个" % (name, host, port, len(models)))
            for m in models[:12]:
                print("        - %s" % m)
            if len(models) > 12:
                print("        ... 还有 %d 个" % (len(models) - 12))
            found.append((name, "http://%s:%d" % (host, port), kind, models))
        except Exception as exc:
            print("  %-16s %s:%-6d 端口开着但读不到模型：%r"
                  % (name, host, port, exc))

    print()
    print("-" * 74)
    # ollama 命令行（可能装了但没在跑）
    import shutil
    import subprocess
    exe = shutil.which("ollama")
    print("PATH 里的 ollama：%s" % (exe or "没有"))
    if exe:
        try:
            r = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=6)
            print("ollama list 返回码 = %d" % r.returncode)
            for line in (r.stdout or "").splitlines()[:15]:
                print("   " + line)
        except Exception as exc:
            print("  跑 ollama list 失败：%r" % (exc,))

    # 模型文件缓存目录（能看出用户到底下过什么）
    print()
    home = os.path.expanduser("~")
    for d in (os.path.join(home, ".ollama", "models"),
              os.path.join(home, ".cache", "lm-studio", "models"),
              os.path.join(home, ".lmstudio", "models"),
              os.path.join(home, ".cache", "huggingface", "hub")):
        if os.path.isdir(d):
            try:
                n = len(os.listdir(d))
            except Exception:
                n = -1
            print("模型目录存在：%s  (%d 项)" % (d, n))

    print()
    print("=" * 74)
    if found:
        print("RESULT: 找到 %d 个可用服务" % len(found))
        for name, base, kind, models in found:
            print("  %s -> %s  (%s, 首选模型 %s)"
                  % (name, base, kind, models[0] if models else "无"))
    else:
        print("RESULT: 没探测到本地推理服务")
        print("  如果你用的是别的端口/别的机器，告诉我地址和端口就行。")
    print("=" * 74)
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
