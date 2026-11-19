【免责声明】
一点自家磨出来的小经验，整理得比较粗糙，仅供参考。我个人对八股比较敏感，难免有些吹毛求疵，清单里不少说法别人或许并不在意，甚至正好喜欢，这都完全没问题。每家的机和每个人的口味都不一样，这里只代表我的个人偏好，不评判任何表达的好坏。如果对你有用，欢迎按自家的情况随意删改。

# 压 AI 八股：口吻清单 + 禁词检查

一套给陪伴类 / 聊天类 AI 压口癖的小工具，两层一起用：

1. **每轮注入口吻清单**：`口吻清单.md` 每轮塞进上下文，告诉模型哪些说法、句式不要用，以及该怎么说。
2. **回复后禁词检查**：回复写完先过一遍规则表，撞上禁词就打回去，让模型整条重写。

只写进人设（系统提示）大概只能压住七八成，长对话、自动压缩之后又会冒出来。每轮注入能把剩下的大部分压住，漏网的再由检查兜底。

## 文件

| 文件 | 用途 |
|---|---|
| `口吻清单.md` | 口吻核心 + 禁词 / 句式清单。文中的“她”指用户，按你家的称呼替换 |
| `hooks/tone_reminder.sh` | UserPromptSubmit hook：每轮把整份 `口吻清单.md` 注入 `additionalContext` |
| `hooks/banned_check.py` | Stop hook：查本轮回复，命中禁词就 `block`，要求整条重写；也能单独跑命令行检查 |
| `hooks/banned_words.json` | 规则表：正则、例外、宏、每条规则自带的单测例句 |
| `通用思路.md` | 不用 Claude Code 时怎么照着做：酒馆、小手机、自己接 API 的都能用 |

`banned_check.py` 的行为：

- 每一轮都查，只查回复正文（不看工具调用和思考）；代码块、行内代码、链接、文件路径、HTML 标签不查。
- 引号「」『』“”里的字不查（是在引用词句，不是自己在说）。
- 回复在讨论这套机制本身时（出现“禁词”“hook”“正则”之类），进入 meta 模式，只查少数几条。
- 每轮最多拦 2 次，第三次还命中就放行并写日志，不会死循环。
- 出任何异常都静默放行，不打断会话。
- 状态和日志在 `~/.claude/hooks/state/`，可用环境变量 `BANNED_CHECK_STATE_DIR` 改。

## 安装（Claude Code）

需要 bash 和 python3（只用标准库）。

```bash
mkdir -p ~/.claude/tone ~/.claude/hooks
cp 口吻清单.md ~/.claude/tone/
cp hooks/tone_reminder.sh hooks/banned_check.py hooks/banned_words.json ~/.claude/hooks/
chmod +x ~/.claude/hooks/tone_reminder.sh ~/.claude/hooks/banned_check.py
python3 ~/.claude/hooks/banned_check.py --selftest   # 应显示 失败 0 句
```

然后在 `~/.claude/settings.json` 里加上 hooks（已有 `hooks` 字段就合并进去）：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "bash ~/.claude/hooks/tone_reminder.sh 2>/dev/null || true" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 -I ~/.claude/hooks/banned_check.py --stop-hook 2>/dev/null || true" }
        ]
      }
    ]
  }
}
```

清单想放别处，就在命令前设 `TONE_FILE=/你的路径/口吻清单.md`。没设时依次找 `~/.claude/tone/口吻清单.md`、脚本同目录、脚本上一级目录。

## 没有 hook 的平台

两层的思路换哪个 AI 都能用，具体做法见 [通用思路.md](通用思路.md)。最省事的是：把 `口吻清单.md` 的全文贴在系统提示 / 人设的**最末尾**（越靠后越不容易被忽略）。没有检查兜底，效果会打折扣；可以隔一阵把聊天记录里的回复拿出来跑 `banned_check.py --text` 或 `--file`，看哪些说法还在冒，再回头补清单。

## 改成自己家的禁词

1. 先改 `口吻清单.md`：加上你不想看到的词和句式，模型每轮都会看到。
2. 再改 `hooks/banned_words.json` 的 `rules`，每条规则：
   - `pattern`：命中正则；`allow`：例外正则（和命中片段重叠就放过）；
   - 可选 `tail_chars`（只在回复结尾才拦）、`max_len`（只在整条很短时查）、`require`（命中片段里还得有这些词才算）、`meta`（讨论机制时也查）、`modes`；
   - 可用宏 `{P}` 标点、`{E}` 后面是句末或标点、`{B}` 前面是句首或标点、`{H}` 一个汉字；
   - `tests.hit` 写应该拦的句子，`tests.pass` 写不该误伤的句子。
3. 跑单测和试句：

```bash
python3 hooks/banned_check.py --selftest
python3 hooks/banned_check.py --text "这笔先记一笔，我认了"
```

`--selftest` 显示“失败 0 句”再装进去。规则宁可漏放也别乱拦：误伤会让模型把正常的话也改得别别扭扭。
