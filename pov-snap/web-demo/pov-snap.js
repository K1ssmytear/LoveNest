// pov-snap.js — AI 视角聊天截图：网页前端通用示例
//
// 思路：直接克隆聊天页里已经渲染好的消息节点，放进一个离屏容器，
// 只改“位置”相关的东西（左右、尖角、头像在哪侧、边距），颜色/头像/名字一概不动，
// 再把这个容器渲染成 PNG。所以主题、气泡、美化都会自动跟着当前设置走。
//
// 依赖：html-to-image（把 DOM 渲染成图片）。也可以换成 modern-screenshot，用法类似（domToBlob）。
// 这只是示例，要按你家前端的结构改：选择器、怎么分辨谁发的、哪些 class 只管位置。
import { toBlob } from 'https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/+esm';

/* 配套 CSS 示例（假设原来是 .row.me = 用户自己、靠右；.row.you = AI、靠左）：
 *
 *   原有规则里，颜色和位置最好本来就分开写：
 *   .row.me  { flex-direction: row-reverse; }          ← 位置
 *   .row.me  .bubble { background: var(--me-bg); }     ← 颜色（跟着人走，截图时不碰）
 *
 *   截图时只加这几条，把“位置”反过来：
 *   .row.pov-reversed.me  { flex-direction: row; }
 *   .row.pov-reversed.you { flex-direction: row-reverse; }
 *   .row.pov-reversed.me  .bubble::before { left: -6px; right: auto; transform: scaleX(-1); }  ← 尖角
 *   .row.pov-reversed.you .bubble::before { right: -6px; left: auto; transform: scaleX(-1); }
 *
 * 不要直接把 .me 换成 .you：如果 .me 同时管颜色，颜色也会跟着换边，这正是要避免的。
 */

const MAX_MESSAGES = 12;

// ── 1. 从 AI 回复里找标记 ────────────────────────────────────────────────
// 标记单独占一行：<prism-snap match="文字片段" context="2"/> 或 <prism-snap last="6"/>
// 返回去掉标记后的文字（拿去正常显示）和解析出的标记列表。
// 注意：如果去掉标记后文字为空，这条消息也要保留，用来放截图卡片。
export function parseSnapMarkers(text) {
  const markers = [];
  const cleanText = String(text || '').replace(/^[ \t]*<prism-snap\b([^>]*?)\/?>[ \t]*$/gim, (_, attrs) => {
    const m = {};
    for (const [, k, v] of attrs.matchAll(/(\w+)\s*=\s*"([^"]*)"/g)) m[k] = v;
    markers.push({
      match: m.match || null,
      context: Math.max(0, parseInt(m.context ?? '2', 10) || 0),
      last: m.last ? Math.max(1, parseInt(m.last, 10) || 1) : null,
      note: m.note || null, // 可选：AI 写一句为什么想留
    });
    return '';
  }).replace(/\n{3,}/g, '\n\n').trim();
  return { cleanText, markers };
}

// ── 2. 按标记挑消息 ──────────────────────────────────────────────────────
// nodes：按时间顺序排好的消息节点，不要包含带标记的那条回复本身。
// match 找最近一条包含这段文字的消息，前后各带 context 条（对称的，所以 AI 要挑中间那句）。
export function selectMessages(nodes, marker, getText = (n) => n.innerText) {
  const norm = (s) => String(s || '').replace(/\s+/g, '').toLowerCase();
  let picked;
  if (marker.match) {
    const needle = norm(marker.match);
    let i = nodes.length - 1;
    while (i >= 0 && !norm(getText(nodes[i])).includes(needle)) i--;
    if (i < 0) return []; // 找不到：在聊天里提示“没找到这段”，别截错的
    picked = nodes.slice(Math.max(0, i - marker.context), i + marker.context + 1);
  } else {
    picked = nodes.slice(-(marker.last || 6));
  }
  return picked.slice(-MAX_MESSAGES);
}

// 找到真正画出聊天背景的那一层，截图跟着当时的背景走（纯色、渐变、壁纸都行）
function chatBackground(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
    const cs = getComputedStyle(n);
    if (cs.backgroundImage !== 'none' || cs.backgroundColor !== 'rgba(0, 0, 0, 0)') {
      return { image: cs.backgroundImage, color: cs.backgroundColor, size: cs.backgroundSize, position: cs.backgroundPosition };
    }
  }
  return { image: 'none', color: '#fff', size: 'auto', position: '0 0' };
}

// ── 3. 渲染截图 ──────────────────────────────────────────────────────────
// container：聊天列表的容器（离屏容器会浅克隆它，保留 class / data-theme 等属性）
// nodes：selectMessages 挑出来的消息节点（一条消息的整行，包含头像和气泡）
// isUser(node)：这条是不是用户发的
// swapPairs：只管位置、可以直接对调的 class，例如 [['align-left', 'align-right']]
export async function renderPovSnap({ container, nodes, isUser, swapPairs = [], pixelRatio = 2 }) {
  const stage = container.cloneNode(false); // 只要外壳：class、data-theme、CSS 变量都还在
  stage.removeAttribute('id');
  const bg = chatBackground(container);
  Object.assign(stage.style, {
    position: 'fixed', left: '-100000px', top: '0',
    width: container.clientWidth + 'px', height: 'auto', maxHeight: 'none', overflow: 'visible',
    backgroundImage: bg.image, backgroundColor: bg.color, backgroundSize: bg.size, backgroundPosition: bg.position,
  });

  for (const node of nodes) {
    const row = node.cloneNode(true);
    row.querySelectorAll('[id]').forEach((el) => el.removeAttribute('id'));
    row.dataset.povSender = isUser(node) ? 'user' : 'ai'; // 身份照旧，给 CSS 备用
    row.classList.add('pov-reversed'); // 位置反过来，交给上面那几条 CSS
    for (const el of [row, ...row.querySelectorAll('*')]) {
      for (const [a, b] of swapPairs) {
        const hasA = el.classList.contains(a), hasB = el.classList.contains(b);
        if (hasA !== hasB) { el.classList.toggle(a); el.classList.toggle(b); }
      }
    }
    // 行内写死的左右边距也是“位置”，一起对调
    const { marginLeft: ml, marginRight: mr } = row.style;
    if (ml || mr) { row.style.marginLeft = mr; row.style.marginRight = ml; }
    stage.appendChild(row);
  }

  // 放在原容器旁边，祖先上的主题选择器（body[data-theme] .chat …）照样生效
  container.parentNode.insertBefore(stage, container.nextSibling);
  try {
    await document.fonts.ready;
    await Promise.all([...stage.querySelectorAll('img')].map((img) =>
      img.complete ? null : new Promise((r) => { img.onload = img.onerror = r; })));
    // 跨域的头像/表情包需要服务器允许 CORS，否则会画不出来
    return await toBlob(stage, { pixelRatio, cacheBust: true });
  } finally {
    stage.remove();
  }
}

// ── 4. 串起来：收到 AI 回复时调用 ────────────────────────────────────────
// 示例：const shots = await handleAiReply(reply.text, { container, history, isUser, swapPairs });
// history 是“这条回复之前”的消息节点。拿到 Blob 后：URL.createObjectURL(blob) 显示成图片卡片，
// 点开放大、可下载；有服务器的话再上传存一份，AI 之后能翻到自己截过哪些。
// 列表如果是虚拟滚动（只渲染屏幕上的几条），就改成从数据里挑消息、用你自己的消息组件渲染进 stage。
export async function handleAiReply(text, { container, history, isUser, swapPairs }) {
  const { cleanText, markers } = parseSnapMarkers(text);
  const shots = [];
  for (const marker of markers) {
    const nodes = selectMessages(history, marker);
    if (!nodes.length) { shots.push({ marker, error: '没找到这段聊天' }); continue; }
    const blob = await renderPovSnap({ container, nodes, isUser, swapPairs });
    shots.push({ marker, blob, note: marker.note });
  }
  return { cleanText, shots };
}
