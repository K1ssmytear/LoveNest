# 服务器端：头像和截图存取

> 这是我们家自建服务器（Prism，一个 Python FastAPI 后端）上的改动，**只作参考**。接口路径、鉴权、存储目录都是我们家的，你家的后端结构不同，照着思路改就行，不要直接套用。

截图本身是在客户端拍的（原因见主 README 的“会踩的坑”）。服务器只负责两件事：

1. 存两个人的头像，让每个客户端都拿得到；
2. 存 AI 拍过的截图，让 AI 之后能翻到自己截过哪些。

## 头像

| 接口 | 作用 |
| --- | --- |
| `GET /api/sessions/{name}/avatar` | 取 AI（会话）的头像。原来就有。 |
| `POST /api/sessions/{name}/avatar` | 上传 AI 的头像。原来就有。multipart，字段名 `file`。 |
| `GET /api/me/avatar` | 取用户本人的头像。**新加**。没有时返回 404。 |
| `POST /api/me/avatar` | 上传用户本人的头像。**新加**。multipart，字段名 `file`。 |

- 用户头像和 AI 头像存在同一个地方，用一个固定的名字区分（diff 里的 `MY_AVATAR_KEY`）。这个名字不要和任何会话名重名。
- 客户端的同步逻辑：设置或更换头像时上传一次；启动时先 `GET`，**只有服务器返回 404、本机又有头像时才补传**，避免拿手机上的旧缓存覆盖服务器上的新头像。

## 截图

| 接口 | 作用 |
| --- | --- |
| `POST /api/sessions/{name}/pov-snaps` | 上传一张截图。multipart，字段名 `file`。只收 PNG，最大 8 MB。返回 `{"path": "/api/sessions/{name}/pov-snaps/<文件名>"}`。 |
| `GET /api/sessions/{name}/pov-snaps` | 列出最近 50 张，新的在前。返回 `{"snapshots": [{"name", "path", "created_at"}]}`。 |
| `GET /api/sessions/{name}/pov-snaps/{filename}` | 取某一张截图。 |

- 存在服务器数据目录下的 `chat_data/pov_snaps/{会话名}/` 里，文件名是随机生成的 32 位十六进制加 `.png`。
- 会话名和文件名都用正则限死了格式，避免路径穿越。
- 上传和列表要求请求头带登录凭证；取头像和取单张截图的两个 `GET` 也接受把凭证放在查询参数里，这样 `<img src="...">` 能直接加载。

## pov-snap.patch

[`pov-snap.patch`](pov-snap.patch) 是对我们家 `server.py` 的 diff，只包含上面标了“新加”的五个接口（用户头像两个、截图三个）。分享前做过的改动：

- 原来写死的用户名换成了常量 `MY_AVATAR_KEY = "me"`；
- 里面用到的 `_tm.save_avatar` / `_tm.find_avatar_path`（我们家存会话头像的函数）、`require_auth` / `_require_auth_qs`（鉴权）、`CHAT_DATA_DIR`（数据目录）都是我们家后端原有的东西，不在 diff 里，你家要换成自己的。
