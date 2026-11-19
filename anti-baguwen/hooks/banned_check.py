#!/usr/bin/env python3
"""
banned_check.py — 回复禁词检查。规则在同目录 banned_words.json（来源：口吻清单.md 里「她不想看到的说法」）。

用法：

  banned_check.py --text "……"            # 打印命中（规则、命中片段、位置、上下文）
  banned_check.py --file reply.txt
  banned_check.py --selftest             # 跑 banned_words.json 里每条规则自带的 hit/pass 例子
  banned_check.py --stop-hook            # Claude Code Stop hook 模式：stdin 读 hook 输入 JSON
  可选：--rules 另一份规则表；--mode chat|talk|scene（默认 chat）

Stop hook 模式：
  * 每一轮都查，只查本轮 assistant 的 text 块，不看 tool_use / thinking；已经查过的块不重查，
    所以被拦后重写的那条只查新写的部分。
  * 每轮最多拦 MAX_BLOCKS 次（第一次 + stop_hook_active 时再一次），之后放行并记日志，防止死循环。
    状态目录：环境变量 BANNED_CHECK_STATE_DIR，默认 ~/.claude/hooks/state。
  * 模式按环境变量 BANNED_CHECK_MODE（默认 chat）。
  * 任何异常都静默放行，不打扰会话。
"""
import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "banned_words.json")
STATE_DIR = os.environ.get("BANNED_CHECK_STATE_DIR") or os.path.expanduser("~/.claude/hooks/state")
LOG = os.path.join(STATE_DIR, "banned_check.log")
HOOK_MODE = os.environ.get("BANNED_CHECK_MODE") or "chat"

MAX_BLOCKS = 2
MASK = ""   # 屏蔽字符（私用区），规则宏 {P} 里也认它
REASON_TAG = "[banned_check]"

# 不查的区域：整段换成 MASK（长度不变，位置仍对得上原文）
MASK_PATTERNS = [
    re.compile(r"```.*?(?:```|$)", re.S),                      # 代码块（含没闭合的）
    re.compile(r"`[^`\n]+`"),                                   # 行内代码
    re.compile(r"</?[a-zA-Z][\w-]*(?:\s[^<>]*)?/?>"),          # HTML/XML 标签本身
    re.compile(r"https?://\S+"),                                 # 链接
    re.compile(r"(?:~|\.{1,2})?/[\w.\-@+~/一-鿿]*[\w\-]"),  # 文件路径
    re.compile(r"\b[\w\-]+\.(?:py|sh|md|json|jsonl|yaml|yml|txt|html|js|ts|log|service|conf)\b"),  # 文件名
]
SENT_END = "。！？!?\n"


# ---------------------------------------------------------------- 规则

class Rule:
    def __init__(self, d, macros):
        self.id = d["id"]
        self.cat = d.get("cat", "")
        self.meta = bool(d.get("meta", False))
        self.tail_chars = d.get("tail_chars")
        self.modes = d.get("modes")
        self.max_len = d.get("max_len")
        try:
            self.re = re.compile(expand(d["pattern"], macros), re.I)
            self.allow = [re.compile(expand(a, macros), re.I) for a in d.get("allow", [])]
            self.require = re.compile(expand(d["require"], macros), re.I) if d.get("require") else None
        except re.error as e:
            raise ValueError(f"规则「{self.id}」正则写错了：{e}") from e
        self.tests = d.get("tests", {})


def expand(p, macros):
    # 宏可以嵌套（E 里用 P），展开几轮
    for _ in range(3):
        for k, v in macros.items():
            p = p.replace("{" + k + "}", v)
    return p


class RuleSet:
    def __init__(self, path=RULES_PATH):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        macros = d.get("macros", {})
        self.rules = [Rule(r, macros) for r in d["rules"]]
        te = d.get("topic_exempt", {}).get("pattern")
        self.topic_re = re.compile(te, re.I) if te else None
        sq = d.get("skip_quotes", {})
        self.skip_quotes = {k: v for k, v in sq.items() if not k.startswith("_")}


def mask_text(text, quote_pairs):
    chars = list(text)

    def blank(a, b):
        for i in range(a, b):
            if chars[i] != "\n":
                chars[i] = MASK

    for pat in MASK_PATTERNS:
        for m in pat.finditer("".join(chars)):
            blank(m.start(), m.end())
    for op, cl in quote_pairs:
        pat = re.compile(re.escape(op) + r"[^" + re.escape(cl) + r"\n]*" + re.escape(cl))
        for m in pat.finditer("".join(chars)):
            blank(m.start(), m.end())
    return "".join(chars)


def sentence_at(text, start, end):
    a = start
    while a > 0 and text[a - 1] not in SENT_END:
        a -= 1
    b = end
    while b < len(text) and text[b] not in SENT_END:
        b += 1
    if b < len(text) and text[b] != "\n":
        b += 1
    s = text[a:b].strip()
    return s if len(s) <= 80 else text[max(a, start - 30):min(b, end + 30)].strip()


def _visible_suffix_counts(masked):
    """out[i] = masked[i:] 里还有几个可见的字（不算标点、空白、屏蔽区）。"""
    out = [0] * (len(masked) + 1)
    for i in range(len(masked) - 1, -1, -1):
        c = masked[i]
        out[i] = out[i + 1] + (1 if (c.isalnum() and c != MASK) else 0)
    return out


def check(text, rs, meta=None, mode="chat"):
    """返回 (hits, meta_mode)。hits: [{id, cat, match, pos, sentence, context}]
    mode: chat / talk / scene，决定哪些引号里的字不查、哪些规则生效（规则的 modes 字段）。"""
    if meta is None:
        meta = bool(rs.topic_re and rs.topic_re.search(text))
    quotes = rs.skip_quotes.get("meta" if meta else (mode or "chat"), rs.skip_quotes.get("chat", []))
    masked = mask_text(text, quotes)
    visible_len = len(re.sub(r"[\s" + MASK + "]", "", masked))
    vis_after = _visible_suffix_counts(masked)
    hits = []
    for r in rs.rules:
        if meta and not r.meta:
            continue
        if r.modes and (mode or "chat") not in r.modes:
            continue
        if r.max_len is not None and visible_len > r.max_len:
            continue
        allow_spans = [(m.start(), m.end()) for a in r.allow for m in a.finditer(masked)]
        for m in r.re.finditer(masked):
            s, e = m.start(), m.end()
            if e <= s:
                continue
            if r.tail_chars is not None and vis_after[e] > r.tail_chars:
                continue
            if any(s < ae and as_ < e for as_, ae in allow_spans):
                continue
            if r.require is not None and not r.require.search(masked[s:e]):
                continue
            hits.append({
                "id": r.id, "cat": r.cat, "match": text[s:e], "pos": s,
                "sentence": sentence_at(text, s, e),
                "context": text[max(0, s - 15):e + 15].replace("\n", "⏎"),
            })
    hits.sort(key=lambda h: h["pos"])
    return hits, meta


# ---------------------------------------------------------------- transcript

def _is_prompt(d):
    """是不是一条真正的用户输入（tool_result / meta / hook 反馈 / 压缩摘要都不算）。"""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str) or not c.strip():
        return False
    s = c.lstrip()
    if s.startswith("Stop hook feedback") or REASON_TAG in c or s.startswith("This session is being continued"):
        return False
    return True


def _assistant_texts(d):
    if d.get("type") != "assistant" or d.get("isSidechain"):
        return []
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return [c] if c.strip() else []
    return [b.get("text", "") for b in (c or [])
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()]


def read_turn(path):
    """返回 (prompt_uuid, records)。records：本轮最后一条真实用户消息之后的每条 assistant 记录 {uuid, texts}。
    Claude Code 一条回复会按内容块拆成多条 jsonl 记录（thinking / text / tool_use 各一条）。"""
    size = os.path.getsize(path)
    for tail in (4_000_000, None):
        with open(path, "rb") as f:
            if tail and size > tail:
                f.seek(size - tail)
                raw = f.read().decode("utf-8", "ignore").split("\n")[1:]
            else:
                raw = f.read().decode("utf-8", "ignore").split("\n")
        entries = []
        for line in raw:
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
        idx = None
        for i in range(len(entries) - 1, -1, -1):
            if _is_prompt(entries[i]):
                idx = i
                break
        if idx is None and tail and size > tail:
            continue
        if idx is None:
            return None, []
        recs = [{"uuid": d.get("uuid", ""), "texts": _assistant_texts(d)}
                for d in entries[idx + 1:]
                if d.get("type") == "assistant" and not d.get("isSidechain")]
        return entries[idx].get("uuid", ""), recs
    return None, []


# ---------------------------------------------------------------- stop hook

def log(msg):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def h(s):
    return hashlib.sha1(s.strip().encode("utf-8")).hexdigest()[:16]


def build_reason(hits):
    lines = [REASON_TAG + " 这条回复里有她不想看到的说法："]
    seen = set()
    for x in hits:
        key = (x["id"], x["sentence"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- 「{x['match']}」（{x['id']}）原句：{x['sentence']}")
        if len(seen) >= 8:
            break
    lines.append("把整条回复重写一遍：这些说法一个都不留，换成直接冲她说的话，意思和要办的事照旧。"
                 "不解释、不道歉、不提这次检查，直接输出重写后的完整回复。")
    return "\n".join(lines)


def stop_hook():
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    path = inp.get("transcript_path") or ""
    sid = re.sub(r"[^\w\-]", "_", str(inp.get("session_id") or "nosession"))
    active = bool(inp.get("stop_hook_active"))
    if not path or not os.path.exists(path):
        return
    puuid, recs = read_turn(path)
    if puuid is None:
        return
    items = [(r, t) for r in recs for t in r["texts"]]   # (记录, text 块)
    last = inp.get("last_assistant_message")
    if isinstance(last, str) and last.strip() and h(last) not in {h(t) for _, t in items[-3:]}:
        items.append((None, last))  # transcript 还没落盘时补上，没有 uuid

    os.makedirs(STATE_DIR, exist_ok=True)
    sp = os.path.join(STATE_DIR, f"banned_{sid}.json")
    try:
        with open(sp, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if st.get("prompt_uuid") != puuid or not active:
        st = {"prompt_uuid": puuid, "blocks": 0, "checked": []}

    # 按「记录 uuid + 内容」认已查过的块：重写版就算一字不改照抄了被拦的句子，也是新记录，照样再查
    key = lambda r, t: ((r or {}).get("uuid") or "pending") + ":" + h(t)  # noqa: E731
    # 上一次查的时候转录还没落盘，那段是按 pending 记的；现在它带着 uuid 落盘了，认成同一段，别再查一遍。
    for r, t in items:
        if r and r.get("uuid") and key(r, t) not in st["checked"] and ("pending:" + h(t)) in st["checked"]:
            st["checked"].remove("pending:" + h(t))
            st["checked"].append(key(r, t))
    fresh = [(r, t) for r, t in items if key(r, t) not in st["checked"]]
    st["checked"] += [key(r, t) for r, t in fresh]
    if not fresh:
        _save(sp, st)
        return
    rs = RuleSet()
    hits, meta = check("\n\n".join(t for _, t in fresh), rs, mode=HOOK_MODE)
    if not hits:
        _save(sp, st)
        return
    summary = "、".join(sorted({x["id"] for x in hits}))
    if st["blocks"] >= MAX_BLOCKS:
        log(f"{sid[:8]} 放行(已拦{st['blocks']}次) 仍命中：{summary}")
        _save(sp, st)
        return
    st["blocks"] += 1
    _save(sp, st)
    log(f"{sid[:8]} 拦第{st['blocks']}次 meta={meta} 命中：{summary} | " +
        " / ".join(x["sentence"] for x in hits[:4]))
    print(json.dumps({"decision": "block", "reason": build_reason(hits)}, ensure_ascii=False))


def _save(sp, st):
    try:
        st["checked"] = st["checked"][-200:]
        st["ts"] = int(time.time())
        tmp = sp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, sp)
    except Exception:
        pass


# ---------------------------------------------------------------- CLI

def print_hits(text, rs, mode="chat"):
    hits, meta = check(text, rs, mode=mode)
    if meta:
        print("(meta 模式：回复在讨论这套机制，引号里不查，只跑 meta=true 的规则)")
    for x in hits:
        print(f"  [{x['cat']}/{x['id']}] 「{x['match']}」 @{x['pos']}  …{x['context']}…")
    if not hits:
        print("  无命中")
    return hits


def _test_mode(s):
    m = re.match(r"^\[(chat|talk|scene)\]", s)
    return (m.group(1), s[m.end():]) if m else ("chat", s)


def selftest(rs):
    bad = 0
    n = 0
    for r in rs.rules:
        for s in r.tests.get("hit", []):
            n += 1
            mode, s = _test_mode(s)
            hits, _ = check(s, rs, meta=False, mode=mode)
            if not any(x["id"] == r.id for x in hits):
                bad += 1
                print(f"漏拦 [{r.id}] {s}")
        for s in r.tests.get("pass", []):
            n += 1
            mode, s = _test_mode(s)
            hits, _ = check(s, rs, meta=False, mode=mode)
            own = [x for x in hits if x["id"] == r.id]
            if own:
                bad += 1
                print(f"误伤 [{r.id}] {s} -> {[x['match'] for x in own]}")
    print(f"规则 {len(rs.rules)} 条，例子 {n} 句，失败 {bad} 句")
    return bad


def main():
    ap = argparse.ArgumentParser(description="回复禁词检查")
    ap.add_argument("--text")
    ap.add_argument("--file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--stop-hook", action="store_true")
    ap.add_argument("--rules", default=RULES_PATH)
    ap.add_argument("--mode", default="chat", choices=["chat", "talk", "scene"])
    a = ap.parse_args()
    if a.stop_hook:
        try:
            stop_hook()
        except Exception as e:  # 永远不打断会话
            log(f"异常 {type(e).__name__}: {e}")
        return 0
    rs = RuleSet(a.rules)
    if a.selftest:
        return 1 if selftest(rs) else 0
    if a.text is not None:
        print_hits(a.text, rs, mode=a.mode)
    elif a.file:
        with open(a.file, encoding="utf-8") as f:
            print_hits(f.read(), rs, mode=a.mode)
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
