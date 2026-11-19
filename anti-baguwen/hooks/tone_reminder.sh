#!/bin/bash
# UserPromptSubmit hook：每轮把口吻清单整份注入 additionalContext。
# 放在每轮上下文里，自动压缩之后也照样有效。
#
# 清单位置：环境变量 TONE_FILE；没设就用 ~/.claude/tone/口吻清单.md；
# 那里也没有，就依次找脚本同目录、上一级目录里的 口吻清单.md（直接从包里跑也能用）。

cat >/dev/null   # 吃掉 hook 输入，用不到

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${TONE_FILE:-$HOME/.claude/tone/口吻清单.md}"
[ -f "$SRC" ] || SRC="$HERE/口吻清单.md"
[ -f "$SRC" ] || SRC="$HERE/../口吻清单.md"
[ -f "$SRC" ] || exit 0

python3 - "$SRC" <<'PY'
import json, sys
try:
    ctx = open(sys.argv[1], encoding="utf-8").read().strip()
except Exception:
    sys.exit(0)
if ctx:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": ctx}}, ensure_ascii=False))
PY
