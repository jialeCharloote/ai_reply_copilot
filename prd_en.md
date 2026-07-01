# AI Reply Copilot MVP PRD

Created: 2026-06-17
Status: Draft
Owner: Charlotte
Target folder: `side_projects/ai_reply_copilot/`

## 1. Executive Summary

AI Reply Copilot is a **Mac desktop companion app**: it reads your iMessage and Slack conversation context directly on your Mac, understands the conversation you are in, drafts 3 reply candidates in your own voice, and lets you pick one, edit it, and send it (or drop it into the input field) right away.

The first MVP focuses on **iMessage + Slack**. The mobile screenshot/OCR flow is not dropped, but demoted to a later/auxiliary path (see sections 9 and 14). The underlying plumbing is the same one OpenClaw uses (read `chat.db` on the Mac, send via Messages automation, use the official Slack API), but the product shape differs: OpenClaw is a bot/gateway you talk to, while this product is a copilot that drafts a reply to the conversation in your own voice.

Why desktop, not mobile: the "read context directly and reply directly" workflow that is impossible on iOS is feasible on the Mac, because iMessage stores messages locally in `~/Library/Messages/chat.db` and Slack has an official API. Rather than forcing users to take an extra screenshot step on the phone, build a Mac copilot that reads the current conversation, drafts in the user's voice, and sends after review. Trust still matters, but it is more defensible: the app is locally installed, permissions are explicitly granted, and it is local-first. The non-negotiable boundaries stay: do not bypass platform restrictions, do not use private APIs that require disabling SIP, and do not auto-send without review.

Positioning:

> On your Mac, the AI understands the conversation you are replying to and gives you 3 replies that sound like you, ready to send after a glance.

## 2. Problem Statement

People often know what they want to say but struggle with wording, tone, timing, and social nuance. This is especially painful in high-stakes or awkward messages: boss, client, investor, recruiter, dating, conflict, apology, negotiation, or cross-cultural communication.

Existing AI writing tools help polish text, but most do not understand the immediate conversation context across many messaging apps. Native OS assistants are moving toward this, but third-party products still have an opportunity to win on speed, personality, private-by-design workflow, and specific reply use cases.

The key product observation: the "read context directly and reply directly" workflow that is impossible on the phone is feasible on the **Mac desktop** - iMessage messages live locally in `chat.db`, and Slack has an official API. This is the path that products like OpenClaw have already proven viable.

## 3. Market Research

### 3.1 Overseas Market

Key signals:

- Messaging and social apps are high-frequency mobile workflows. Any tool that reduces reply friction can ride an existing daily behavior instead of creating a new habit.
- Consumer AI adoption is no longer niche. a16z's 2025 consumer AI report ranked AI-first mobile apps by monthly active users and noted that ChatGPT mobile had reached large-scale recurring usage, with 175M mobile users among 400M weekly active users at the time of the report.
- The market is converging from three directions:
  - OS-level assistants: Apple Intelligence, Siri AI, Gemini, Samsung/Android AI layers.
  - Keyboard/writing layers: Grammarly, Microsoft SwiftKey, Gboard-style writing assistance.
  - Inbox/chat aggregators: Beeper, Texts-style unified inbox products, Slack AI.

Relevant overseas products:

| Product | Category | What it proves | Gap / opportunity |
| --- | --- | --- | --- |
| Apple Intelligence / Siri AI | OS-level assistant | Apple is explicitly building writing help "virtually anywhere you type," plus Messages/Mail tone matching and contextual suggestions. | Strongest platform threat. Opportunity is niche speed, non-Apple support, sharper personality, and high-stakes reply workflows. |
| Grammarly Mobile | AI writing keyboard | Cross-app writing help works in iOS/Android apps, including iMessage, WhatsApp, Instagram, X, LinkedIn, Gmail, Outlook. | Focuses on writing quality and rewrite, not deep conversation-context reply generation. |
| Microsoft SwiftKey | Mobile keyboard | Personalized autocorrect/prediction and toolbar distribution are mature. | More typing infrastructure than context-aware reply copilot. |
| Wispr Flow | Voice-to-text input layer | Users will adopt an input-layer tool if it works in every app and speeds up communication. | Voice-first, not screenshot/context-first reply suggestions. |
| Beeper | Unified chat inbox | Users with many channels want one place to read/reply across WhatsApp, Instagram, Telegram, Signal, Messenger, X, Google Messages, LinkedIn, Discord, Slack. | Not a native overlay inside every app; platform integration risk is high. |
| Slack AI | Enterprise in-app AI | Summaries, recaps, workspace search, and AI agents are valuable in business communication. | Limited to Slack workspace context, not personal/social cross-app communication. |
| Dating reply assistants / Rizz-style apps | Vertical AI reply help | Users already pay or download tools for awkward emotional messaging. | Often narrow, trust-sensitive, and can feel gimmicky. Opportunity is a more general and privacy-forward assistant. |

Overseas conclusion:

The broad horizontal "AI writing anywhere" market is crowded and increasingly platform-owned. A small MVP should not compete head-on with Apple or Grammarly. The sharper wedge is "context-aware replies for awkward/high-stakes messages," starting on the Mac desktop where iMessage and Slack context can be read directly, and expanding later to more channels and mobile.

### 3.2 China Market

Key signals:

- China has a massive mobile-first communication base. CNNIC's 57th report says China's internet users reached 1.125B by December 2025 and internet penetration reached 80.1%.
- Generative AI adoption in China is growing quickly. CNNIC reported 602M generative AI users by December 2025; its 2025 generative AI report showed 515M users by June 2025, with 74.6% of users under 40 and over 90% of users primarily choosing domestic large models.
- China is especially attractive for chat/social reply workflows because WeChat, DingTalk, Feishu, QQ, Xiaohongshu, Douyin, and enterprise messaging sit at the center of daily communication.
- China is also more complex because of model备案, content safety, PIPL, and platform ecosystem constraints. Public-facing generative AI services in China require a stronger compliance path than a simple overseas prototype.

Relevant China products:

| Product | Category | What it proves | Gap / opportunity |
| --- | --- | --- | --- |
| 讯飞输入法 | Input method | Large incumbent input method with speech/input distribution and Chinese typing strengths. | Incumbent can add AI features, but may be broad utility rather than emotionally intelligent reply specialist. |
| 百度输入法 | Input method | Baidu has both input distribution and foundation model/search assets. | Strong China-platform competitor; differentiation must be UX/personality/privacy, not base AI. |
| 搜狗输入法 | Input method | Long-standing Chinese input method distribution and Tencent ecosystem relevance. | May compete through distribution if AI reply becomes a default input feature. |
| 微信输入法 / WeChat ecosystem | Platform-adjacent input/social layer | WeChat is the most important communication platform; any native AI reply feature would be a major threat. | Third-party opportunity is limited inside WeChat unless workflow is screenshot/share/manual paste. |
| 豆包 / Kimi / 通义千问 / 腾讯元宝 / DeepSeek | General AI assistants | General assistants already solve "help me write this reply" if users manually paste context. | They are not optimized for one-tap reply generation inside social conversations. |
| 钉钉 / 飞书 AI | Enterprise collaboration AI | Chinese enterprise communication apps are making AI a work entry point. | In-app only; not cross-app personal communication. |

China conclusion:

China is attractive for scale but risky for an early independent launch. The better near-term strategy is to validate the core workflow in an overseas/iOS-friendly prototype first, then decide whether to localize with a domestic model/provider and compliance path. If launching in China, start as a personal productivity tool with user-initiated screenshot/copy input, no automatic scraping, no auto-send, no public content generation feed.

## 4. Target Users

Primary persona: heavy iMessage + Slack user on the Mac

- Spends most of the workday at a Mac, with iMessage and Slack as primary channels.
- Often rewrites messages several times before sending.
- Needs help with tone, not just grammar.
- Wants replies that sound natural, not robotic.
- Comfortable with desktop workflows and willing to grant permissions to a local, controllable assistant.

Secondary persona: professional communicator

- Founder, PM, sales, recruiter, consultant, job seeker, creator, or customer-facing operator.
- Needs to respond quickly while protecting reputation.
- Values professional, concise, empathetic responses.

Secondary persona: bilingual / cross-cultural communicator

- Communicates in English and Chinese or across cultural contexts.
- Needs tone localization, not literal translation.
- Wants to avoid sounding too blunt, too cold, too casual, or too formal.

## 5. Jobs To Be Done

- When I get a difficult iMessage/Slack message on my Mac, I want suggested replies in several tones right there, without overthinking and without taking a screenshot.
- When I need to sound professional, I want the product to rewrite my rough intent into a polished reply so I can protect my reputation.
- When I am joking/flirting/softening a message, I want the assistant to preserve context and social nuance so I do not sound awkward.
- When I communicate in a second language, I want tone-aware localization so I can sound natural.
- When I am happy with a candidate, I want to send it after a glance instead of copy-pasting back into the original app.

## 6. MVP Scope

### 6.1 Core MVP Workflow (Mac desktop)

1. On first run, the user connects and authorizes each channel:
   - iMessage: grant Full Disk Access (read `chat.db`) and Automation permission (send via `Messages.app`).
   - Slack: authorize via the official Slack App (Socket Mode); read and send within the granted scopes.
2. The app identifies the conversation the user is currently viewing/replying to (active iMessage thread or Slack channel/DM) and reads recent context.
3. The app shows a one-sentence understanding of the conversation and generates 3 reply candidates by default; the user can optionally adjust intent and tone:
   - Intent: reply, decline, apologize, follow up, clarify, flirt, negotiate, de-escalate.
   - Tone: professional, friendly, funny, serious, concise, warm, confident, flirty.
4. The user picks one candidate and can edit it directly.
5. After review, the user either:
   - sends in one tap: iMessage via `Messages.app` automation, Slack via API; or
   - drops the reply into the input field for the user to send manually.
6. The user can rate the result, which informs tone and the personal style profile.

### 6.2 MVP Non-Negotiables

- All channel reads are based on explicit user authorization (Full Disk Access / Slack OAuth), with a clear statement of what is read.
- Local-first: context is processed locally, not retained long-term by default, and not used to train models.
- No auto-send without review; the user sees and can edit every message before it goes out.
- No iMessage advanced operations that require disabling SIP or injecting private APIs (threaded reply, tapback, unsend, etc.).
- Replies must be editable before use.
- Sensitive content warning when a conversation appears to contain personal, financial, medical, legal, or workplace-confidential information.

## 7. Goals

1. Reduce time to draft a reply by at least 50% for tested high-stakes message scenarios.
2. Achieve at least 40% activation among beta users, defined as generating and sending (or inserting) at least one reply within the first session.
3. Achieve at least 25% week-1 retention among beta users who generated 3+ replies in the first week.
4. Reach at least 60% "this sounds like something I would send" rating among sent replies.
5. Validate at least one high-intent wedge: professional replies, dating/social replies, bilingual replies, or conflict/apology replies.

## 8. Non-Goals

- No iMessage advanced operations that require disabling SIP or injecting private APIs (threaded reply, tapback, edit, unsend, group ops).
- No auto-send without review; the user confirms before sending.
- No mobile-first experience in MVP (channels beyond iMessage/Slack, and the phone screenshot/OCR flow, are later/auxiliary).
- No training on user conversations by default. This is essential for trust.
- No enterprise Slack admin integration in MVP. It is a different buyer and compliance motion.
- No China public launch before compliance review. Domestic generative AI service requirements and content controls need a separate plan.

## 9. Requirements

### P0 Must-Have

| Requirement | Acceptance Criteria |
| --- | --- |
| iMessage context read | With Full Disk Access granted, the app reads `chat.db` and reconstructs recent context for the current conversation. |
| Slack context read | After authorizing the official Slack App, the app reads recent messages from channels/DMs the user has access to. |
| Current conversation detection | The app locates the active thread/channel the user is replying to and pulls its context. |
| 3 reply candidates | App returns 3 meaningfully different replies, generated by default without first picking intent/tone. |
| Tone selector | User can select at least 5 tones: professional, funny, serious, concise, warm. |
| Intent selector | User can select at least 5 intents: reply, decline, apologize, follow up, clarify. |
| Edit reply | User can edit any candidate before sending. |
| iMessage send | Send plain text via `Messages.app` automation; requires user confirmation before sending. |
| Slack send | Send the reply to the channel/DM via the Slack API; requires user confirmation before sending. |
| Insert into input field | User can choose not to send directly, but to drop the reply into the input field to send manually. |
| Privacy and permissions notice | Clearly states what data is read, local-first, no training by default, no auto-send without review. |
| Feedback buttons | User can rate each reply: useful, too robotic, wrong tone, unsafe, not my style. |

### P1 Nice-To-Have

| Requirement | Acceptance Criteria |
| --- | --- |
| Personal style profile | User can set preferred style: direct, warm, witty, concise, emoji/no emoji. |
| Bilingual output | User can generate English, Chinese, or bilingual replies. |
| Conversation summary | App shows a 1-sentence summary of what it thinks is happening before drafting. |
| Saved snippets | User can save reusable professional/dating/customer-service patterns. |
| Proactive nudge | When a new message arrives and seems "hard to answer," the desktop app proactively offers one-tap candidates. |
| More desktop channels | Extend to other readable/sendable channels on the Mac (WhatsApp desktop, Telegram, etc.). |

### P2 Future Considerations

| Requirement | Notes |
| --- | --- |
| Mobile screenshot / OCR flow | Mobile auxiliary path: share-sheet screenshot + auto OCR, for when the user is away from the Mac. |
| Mobile companion app | Lightweight iOS/Android version for phone-primary users. |
| Android floating window / live screen OCR | MediaProjection screen-capture + on-device OCR for live recognition; Android-only, requires clear permission disclosure. |
| Local/on-device OCR and small model | Useful for privacy-sensitive users and lower latency. |
| Gmail/Discord connector | Only after explicit OAuth consent; read-only first. |
| Enterprise version | Admin controls, data retention policies, SOC 2 posture, model routing. |
| China localized version | Domestic model,备案/compliance review, China-specific content policy, app store distribution strategy. |

## 10. UX Principles

- Effortless: no screenshots or copy-paste - the app reads the current conversation on the Mac, the user only "reviews candidates, edits, sends."
- Fast: useful replies within 10 seconds after locating the conversation.
- Trustworthy: permissions explicitly granted, clear disclosure of what is read, no auto-send without review, local-first.
- Human-sounding: suggestions should feel like drafts from the user, not generic AI.
- Controllable: user can see the context the AI read, edit each reply, and choose to send or only insert.
- Low-friction: "read conversation -> see 3 candidates -> review/edit -> send" should be the default path.

## 11. Information Architecture

Primary surfaces (Mac desktop app, can live in the menu bar / side panel):

1. Connections and Permissions
   - iMessage: Full Disk Access, Automation onboarding
   - Slack: official App authorization
   - Connected-channel status
2. Conversation List / Current Conversation
   - Active iMessage threads, Slack channels/DMs
   - Recent context the AI read
   - One-sentence conversation understanding
3. Reply Builder
   - 3 candidates by default
   - Optional: intent selector, tone selector, "what I want to say" input
4. Results and Send
   - 3 reply cards
   - Edit, regenerate, rate
   - One-tap send / insert into input field
5. Style Profile
   - Preferred tone
   - Emoji preference
   - Formality level
   - Languages
6. Privacy / Data Controls
   - View/delete local history
   - Disable history
   - Manage per-channel permissions
   - Export data

## 12. Data and AI Behavior

### Input

- Thread history of the current conversation (from iMessage `chat.db` or the Slack API)
- Conversation metadata (who the other party is, group vs DM, channel, language)
- Optional user intent (default: plain reply)
- Optional tone selection
- Optional style profile
- Optional "what I want to say" input

### Output

- Conversation understanding summary
- 3 reply candidates
- Optional explanation of tone differences

### Prompting Rules

- Preserve user intent and context.
- Do not invent facts.
- Avoid over-apologizing unless the selected intent is apology.
- For professional replies: concise, clear, no unnecessary enthusiasm.
- For funny replies: light humor, no insult unless user explicitly asks and content is safe.
- For flirty replies: respectful, non-explicit, age-safe.
- For conflict replies: de-escalate and clarify next step.

### Safety Rules

- Do not generate harassment, threats, manipulation, impersonation, scams, or non-consensual sexual content.
- For legal/medical/financial topics, suggest cautious wording and recommend professional advice where appropriate.
- For workplace-confidential conversations, warn users before cloud processing and allow local-only handling.

## 13. Technical Approach

Recommended MVP stack (Mac desktop first):

- Desktop shell: native Swift/SwiftUI, or Tauri / Electron (depending on team familiarity and bundle-size tradeoffs).
- iMessage read: read the local `~/Library/Messages/chat.db` (SQLite); requires Full Disk Access.
- iMessage send: drive `Messages.app` via Automation permission (AppleScript / automation) to send plain text.
- Slack: official Slack App + Socket Mode (or webhook), reading and sending within the granted scopes.
- LLM: start with a hosted model API for quality and iteration speed; support local-only / optional local models for sensitive conversations.
- Storage: local-first by default; history can be disabled and deleted; optional encrypted cloud sync later.
- Backend: lightweight API for generation, feedback logging, and rate limiting (context can be pre-processed locally before the call).

Recommended architecture:

```text
Mac Desktop App
  -> iMessage chat.db read / Slack API read
  -> Current conversation detection + context assembly
  -> Generation (3 candidates + one-sentence understanding)
  -> Review / Edit
  -> Send (Messages automation / Slack API) or insert into input field
  -> Feedback
```

Do not build iMessage advanced operations that require disabling SIP or injecting private APIs (threaded reply, tapback, unsend, etc.); the MVP only sends plain text via Messages automation. The mobile approach (screenshot + OCR) is a later auxiliary path, not the fastest way to validate demand.

## 14. Platform Constraints

### macOS (primary battlefield)

- iMessage messages live locally in `~/Library/Messages/chat.db`; readable after granting **Full Disk Access**. Sending is done by driving `Messages.app` via **Automation** permission. This is the feasibility basis of the MVP.
- iMessage advanced native operations (threaded reply, tapback, edit, unsend, group ops) require injecting private APIs (a dylib into `Messages.app` calling internal `IMCore`) and **disabling SIP** - high risk and high friction, explicitly out of scope.
- **No Mac App Store**: apps that read a private database via Full Disk Access generally cannot pass Mac App Store review, so distribute via direct download + notarization, and make the permission disclosure transparent.
- Slack uses the official App + Socket Mode, reading/writing within requested scopes - compliant and controllable; needs token management and workspace authorization.

### iOS / Android (later, auxiliary)

- iOS does not allow third-party apps to read other apps' screens or message content, so there is no desktop-style direct read on mobile; the phone can only use the screenshot + auto-OCR (share sheet) auxiliary path.
- Android is more flexible (MediaProjection screen-capture OCR, Accessibility screen reads, IME) but permission-sensitive and in tension with the trust positioning; treat as a later experiment.
- Conclusion: the direct-read + direct-send capability only holds on the Mac desktop, so the first MVP bets on the Mac.

## 15. Competitive Positioning

### What We Should Not Claim

- "Bypasses platform restrictions to read everything."
- "Auto-sends replies for you without review."
- "Replaces Grammarly/Apple Intelligence."
- "Reads all of your apps' messages without permission."

### What We Should Claim

- "Understands the iMessage/Slack conversation you are replying to, right on your Mac."
- "Drafts 3 candidates in your voice: professional, funny, serious, warm, concise."
- "You stay in control: see the context, edit, choose to send or just insert."
- "Built for awkward and high-stakes messages - no screenshots."

### Difference from OpenClaw

OpenClaw has proven that on the Mac you can read iMessage directly (`chat.db` + Full Disk Access), send via Messages automation, and read/send Slack via the official API - this product reuses the same plumbing. But the product shape differs:

| Dimension | OpenClaw | This product |
| --- | --- | --- |
| Shape | A bot / multi-channel gateway you talk to | A copilot that drafts your reply to the current conversation |
| Who speaks | The assistant acts as an entity that converses with you/others | Drafts candidates you review, then send as yourself |
| Core value | An always-reachable personal assistant | Tone and social judgment for high-stakes replies |
| User action | Give the assistant instructions | See 3 candidates, edit, send |

### Differentiation (vs generic writing tools)

| Dimension | Incumbents | Our MVP |
| --- | --- | --- |
| Context capture | OS-native or limited to current text field | Reads the current iMessage/Slack conversation directly on the Mac |
| Tone | Generic rewrite/tone | Reply-specific tones and intents, matched to personal style |
| Trust | Often requires keyboard/full access | Explicit authorization, local-first, review before send |
| Use case | General writing | High-stakes social/professional replies |
| Speed | Varies | Candidates as soon as the conversation is read; one-tap send |

## 16. Success Metrics

Leading indicators:

- Activation: % of new users generating and sending (or inserting) a reply in first session.
- Time to value: median time from locating the conversation to sending.
- Reply usefulness: % of generated replies rated useful.
- Regeneration rate: high regeneration may indicate weak first outputs.
- Send rate by tone: identifies strongest use cases.

Lagging indicators:

- Week-1 and week-4 retention.
- Average replies generated per active user per week.
- Conversion to paid.
- Churn reason by wedge.
- Referral rate / organic sharing.

Initial beta targets:

- 40% first-session activation.
- Median conversation-to-send under 45 seconds.
- 60% useful rating on sent replies.
- 25% week-1 retention among activated users.
- At least one segment with 3+ reply generations per week.

## 17. Monetization Hypotheses

Freemium:

- Free: 10 reply generations/week.
- Paid: unlimited replies, style profile, bilingual tone localization, saved snippets.

Possible pricing:

- Overseas consumer: $5.99-$9.99/month.
- Professional/prosumer: $12-$19/month if tied to LinkedIn, Slack, email, sales, recruiting.
- China consumer: lower ARPU; consider ¥18-¥38/month if localized and compliant.

Best first monetization wedge:

- Professional/social anxiety productivity, not pure novelty.
- Dating-only could convert but may create brand and safety issues.
- Enterprise Slack version should wait until there is consumer/prosumer evidence.

## 18. Go-To-Market

### Overseas

Initial channels:

- TikTok/Reels demos: "A hard iMessage/Slack comes in on my Mac, the AI gives 3 replies, I glance and send."
- Product Hunt / Reddit / indie hacker communities (active Mac-tooling audience).
- Job seeker and founder communities.
- Dating/social anxiety content, but positioned tastefully.
- A web demo where users paste a conversation to feel the generation quality, as a low-friction surface before downloading the Mac app.

Beachhead segments:

1. Professionals: boss/client/recruiter replies.
2. Bilingual users: English/Chinese tone localization.
3. Dating/social: flirty but respectful suggestions.
4. Creators/DM managers: reply to followers and brand messages.

### China

Initial channels if localized:

- 小红书: "高情商回复", "职场回复", "英文消息怎么回".
- 抖音/B站 short demos.
- 留学生/跨境工作 users.
- WeChat mini-program as an experiment only if platform rules allow the workflow.

China caution:

- Avoid "自动读取微信聊天记录" positioning.
- Avoid claims that imply bypassing platform restrictions.
- Use "用户主动上传截图/复制文本" framing.
- Use domestic model and compliance review before public China launch.

## 19. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Apple/Google ship native version | High | Focus on high-stakes reply workflows, personality, cross-channel, speed. |
| Privacy concern from reading messages | High | Local-first defaults, explicit authorization, clear disclosure of read scope, no training by default, deletion. |
| iMessage relies on chat.db / Messages automation, may change with OS updates | High | Use only stable paths (read DB + plain-text send), no private APIs / SIP; monitor OS updates. |
| Cannot ship on the Mac App Store | Medium | Direct download + notarized distribution; make permission disclosure transparent. |
| Generic AI output feels fake | High | Style profile, feedback loop, small set of excellent prompt templates. |
| China compliance burden | High | Do not launch public China version until domestic compliance path is defined. |
| Low retention after novelty | Medium | Focus on recurring high-stakes segments and saved style/profile. |

## 20. MVP Timeline

Suggested 6-week MVP:

Week 1:

- Get the iMessage read prototype working: read `chat.db`, reconstruct current conversation context.
- Build prompt templates; test 20 real message scenarios manually.

Week 2:

- Build the Mac desktop app shell (menu bar / side panel).
- Build permission onboarding (Full Disk Access, Automation) and the conversation list / current conversation view.

Week 3:

- Integrate Slack (Socket Mode) for reading.
- Build generation and results UI: 3 candidates, one-sentence understanding, edit, regenerate.

Week 4:

- Wire up sending: iMessage via Messages automation, Slack via API; add insert-into-input-field.
- Add review-before-send, feedback logging, style profile v0, privacy/data controls.

Week 5:

- Notarize and package; beta test with 20-50 users.
- Measure activation, send rate, reply usefulness, and top use cases.

Week 6:

- Improve best-performing wedge.
- Decide next bet: more desktop channels, bilingual localization, mobile auxiliary flow, or professional workflow.

## 21. Open Questions

- Which first wedge should dominate the brand: professional replies, awkward social replies, bilingual replies, or dating?
- Which channel should be the first to ship in week 1: iMessage or Slack?
- Should the desktop shell be native Swift or Tauri/Electron?
- Default send behavior: one-tap direct send, or default to insert and require one more tap to send?
- Should history be disabled by default, or should saved conversation history be opt-in?
- Which model/provider gives the best tone quality for English + Chinese?
- What is the minimum compliance checklist before any China public launch?
- Should the product name be playful or professional?
- When do we start the mobile auxiliary flow (screenshot + OCR), and what signal justifies the investment?

## 22. Recommendation

Build the MVP as a Mac desktop copilot (iMessage + Slack) that reads context directly and sends after review - not as a phone screenshot flow or a full keyboard. The strongest first version is:

> Read the current conversation context -> get 3 replies (optionally adjust intent/tone) -> review/edit -> one-tap send or insert into the input field.

The first validation goal is "do users repeatedly need help replying to high-stakes messages enough to let a Mac assistant that reads the current conversation draft in their voice and send after a glance?" If yes, then invest in deeper surfaces: more desktop channels, bilingual localization, the mobile auxiliary flow (screenshot + OCR), Gmail/Discord connectors, and a China localized version.

## 23. Source Notes

- OpenClaw official page: https://openclaw.ai/
- OpenClaw docs (incl. iMessage / Slack channels): https://docs.openclaw.ai/channels/imessage
- OpenClaw GitHub: https://github.com/openclaw/openclaw
- Apple Intelligence official page: https://www.apple.com/apple-intelligence/
- Grammarly Mobile official page: https://www.grammarly.com/mobile
- Microsoft SwiftKey official page: https://www.microsoft.com/en-us/swiftkey
- Wispr Flow official page: https://wisprflow.ai/
- Beeper official page: https://www.beeper.com/
- Slack AI official page: https://slack.com/features/ai
- Apple Developer custom keyboard documentation: https://developer.apple.com/library/archive/documentation/General/Conceptual/ExtensibilityPG/CustomKeyboard.html
- Android Developers IME documentation: https://developer.android.com/develop/ui/views/touch-and-input/creating-input-method
- CNNIC 57th China Internet Development Statistical Report page: https://www.cnnic.net.cn/n4/2026/0304/c88-11549.html
- CNNIC Generative AI Application Development Report 2025 page: https://www.cnnic.net.cn/n4/2025/1021/c88-11391.html
- CNNIC 56th China Internet Development Statistical Report page: https://www.cnnic.net.cn/n4/2025/0721/c88-11328.html
- CAC Interim Measures for Generative AI Services: https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm
- EU data protection legal framework: https://commission.europa.eu/law/law-topic/data-protection/legal-framework-eu-data-protection_en
- California CCPA page: https://oag.ca.gov/privacy/ccpa
- a16z Top 100 Gen AI Consumer Apps, 4th edition: https://a16z.com/100-gen-ai-apps-4/
- 讯飞输入法官网: https://srf.xunfei.cn/
- 百度输入法官网: https://shurufa.baidu.com/
- 搜狗输入法官网: https://shurufa.sogou.com/
- 豆包官网: https://www.doubao.com/
- Kimi 官网: https://www.kimi.com/
- 千问官网: https://www.qianwen.com/
- 腾讯元宝官网: https://yuanbao.tencent.com/
- 钉钉官网: https://www.dingtalk.com/
