// PovSnapExample.swift
// 示范写法，不是哪个 App 的源码。照思路改进你自己的聊天界面。
// 需要 iOS 16+（ImageRenderer）。

import SwiftUI

// MARK: - 1. 一个环境值：只管“排在哪边”

private struct PovReversedKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// 为 true 时，气泡左右对调；颜色、头像、名字都不受影响。
    var povReversed: Bool {
        get { self[PovReversedKey.self] }
        set { self[PovReversedKey.self] = newValue }
    }
}

// MARK: - 2. 气泡：把“谁发的”和“排在哪边”拆开

struct ChatMessage: Identifiable {
    let id: String
    let fromAI: Bool          // 谁发的：决定颜色、头像、名字
    let text: String
}

struct MessageRow: View {
    let message: ChatMessage
    @Environment(\.povReversed) private var povReversed

    // 平时：用户在右，AI 在左。截图时取反。
    private var onRight: Bool { message.fromAI == povReversed }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if onRight { Spacer(minLength: 40) } else { avatar }
            Text(message.text)
                .padding(.horizontal, 12).padding(.vertical, 8)
                // 跟着人走：颜色只看 fromAI，不看 onRight
                .background(message.fromAI ? Color(.systemGray5) : Color.accentColor)
                .foregroundStyle(message.fromAI ? Color.primary : Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            if onRight { avatar } else { Spacer(minLength: 40) }
        }
        .padding(.horizontal, 12)
    }

    // 跟着人走：头像只看 fromAI
    private var avatar: some View {
        Image(message.fromAI ? "ai_avatar" : "my_avatar")
            .resizable().frame(width: 36, height: 36)
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

// MARK: - 3. 离屏拍图：复用同一个 MessageRow，只打开 povReversed

@MainActor
func renderPovSnap(_ messages: [ChatMessage], width: CGFloat, background: Color) -> UIImage? {
    let content = VStack(spacing: 10) {
        ForEach(messages) { MessageRow(message: $0) }
    }
    .padding(.vertical, 16)
    .frame(width: width)
    .background(background)              // 用你当前的聊天背景
    .environment(\.povReversed, true)    // 只改位置

    let renderer = ImageRenderer(content: content)
    renderer.scale = UIScreen.main.scale
    return renderer.uiImage
}

// MARK: - 4. 认出 AI 回复里的标记

struct SnapRequest {
    var match: String?
    var context = 2
    var last: Int?
}

/// 从回复里找出 <prism-snap .../>，返回去掉标记后的文字和截图请求。
func parseSnapMarker(in reply: String) -> (text: String, request: SnapRequest?) {
    let pattern = #"<prism-snap\s+([^>]*?)/>"#
    guard let regex = try? NSRegularExpression(pattern: pattern),
          let hit = regex.firstMatch(in: reply, range: NSRange(reply.startIndex..., in: reply)),
          let attrRange = Range(hit.range(at: 1), in: reply),
          let whole = Range(hit.range, in: reply) else { return (reply, nil) }

    var req = SnapRequest()
    let attrRegex = try! NSRegularExpression(pattern: #"(\w+)="([^"]*)""#)
    let attrs = String(reply[attrRange])
    for m in attrRegex.matches(in: attrs, range: NSRange(attrs.startIndex..., in: attrs)) {
        guard let k = Range(m.range(at: 1), in: attrs), let v = Range(m.range(at: 2), in: attrs) else { continue }
        switch attrs[k] {
        case "match":   req.match = String(attrs[v])
        case "context": req.context = Int(attrs[v]) ?? 2
        case "last":    req.last = Int(attrs[v])
        default: break
        }
    }
    var text = reply
    text.removeSubrange(whole)
    return (text.trimmingCharacters(in: .whitespacesAndNewlines), req)
}

/// 按请求挑消息：match 取最近一处匹配、前后各带 context 条；last 取最近 N 条。最多 12 条。
func selectMessages(_ all: [ChatMessage], for req: SnapRequest) -> [ChatMessage] {
    if let n = req.last { return Array(all.suffix(min(n, 12))) }
    guard let key = req.match,
          let i = all.lastIndex(where: { $0.text.contains(key) }) else { return [] }
    let lo = max(0, i - req.context), hi = min(all.count - 1, i + req.context)
    return Array(all[lo...hi].prefix(12))
}
