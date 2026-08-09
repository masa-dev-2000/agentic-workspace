---
name: draft-natural-japanese-email
description: Draft or revise natural Japanese business emails that remain clear and commercially precise without sounding stiff, and save or update a reviewable Gmail draft without sending when explicitly requested. Use for 見積書・請求書・資料の送付、依頼、確認、日程調整、お礼、催促、返信、社外メール、既存文面の「柔らかく」「自然に」「固さを取って」という修正、「下書きを作って」「Gmailに保存して」という依頼。
---

# Natural Japanese Business Email

Write polite Japanese that sounds like a real working relationship, not a legal notice or ceremonial letter.

## Default outcome

- Preserve facts, scope, exclusions, dates, prices, attachments, and requested actions.
- Make the tone warm, direct, and conversational while retaining `です・ます`.
- Minimize the recipient's effort to understand and respond.
- Default to a soft business tone unless the user requests formal, firm, casual, or chat-like wording.
- Return text only unless the user explicitly asks to create, save, or update a mailbox draft.

## Workflow

1. Extract the communication essentials:
   - recipient and relationship;
   - purpose;
   - facts or decisions that must not change;
   - attachment or deliverable;
   - included and excluded scope;
   - response or action requested;
   - deadline, if any.
2. Lead with the purpose after a short greeting.
3. Preserve substantive detail. Do not make the message vague merely to soften it.
4. Use short paragraphs and familiar business language.
5. End with one easy-to-answer next action.
6. Check that the softened wording did not weaken a commercial boundary or introduce an unintended promise.
7. Choose the delivery path:
   - return subject and body for a wording request;
   - resolve the stable `email.draft.manage` capability to the connected provider for an explicit draft-save request.
8. For a reply, read the latest relevant thread context before drafting and preserve the real message ID so the draft remains threaded.
9. Verify a saved draft from the tool result. Never treat generated wording alone as proof that a Gmail draft exists.

## Tone rules

Prefer:

- `先日はありがとうございました`
- `お話しした内容をもとに、見積書を作成しましたのでお送りします`
- `今回は〇〇までを対象としています`
- `気になる点や認識の違いがあれば、お知らせください`
- `内容に問題なければ、この内容で進めさせてください`

Avoid by default:

- `ご査収ください`
- `幸甚に存じます`
- `賜りますようお願い申し上げます`
- `何卒よろしくお願い申し上げます`
- repeated `お手数をおかけしますが`
- excessive honorific stacking
- cold constructions such as `問題ございませんでしたら、ご承認をお願いいたします`

Use formal wording only when the recipient, legal context, complaint, payment demand, or executive protocol requires it.

## Preserve scope while softening

For estimates, proposals, and deliverables, keep these explicit:

- what is included;
- what is not included;
- assumptions supplied by the client;
- what counts as completion or delivery;
- what the recipient should do next.

Translate stiffness, not substance.

Example:

- Stiff: `領域決定および切れ込み位置の決定は本業務の対象外となります。`
- Natural: `領域決定と切れ込み位置の決定については、今回は対象に含めていません。`

## Gmail draft handoff

Treat `下書きを作って`, `Gmailに保存して`, or an equivalent explicit request as authority to create exactly one reversible draft. A request for `文面`, `メール案`, or `返信案` authorizes text generation only.

For a new draft:

1. Resolve `To` from the user's instruction or verified mailbox context. Never invent an address.
2. Preserve explicitly supplied `CC`, `BCC`, subject, facts, links, and attachments.
3. Create one draft with `email.draft.manage`; the current Gmail executor is `create_draft`.

For a reply draft:

1. Read the latest message and enough thread context to identify the current ask, participants, and tone.
2. Use the actual Gmail message ID as `reply_message_id`; never substitute a thread ID, email address, subject, URL, or placeholder.
3. Reply only to the sender by default when wider participation is unnecessary. Use reply-all only when the wider recipient set materially needs the response.

When revising a saved draft, update the referenced draft in place with the provider's draft-update action (`update_draft` for Gmail). Do not create a duplicate unless the user explicitly asks for a separate version. If the provider cannot update that draft (for example, Gmail does not edit drafts that already contain attachments), return the revised paste-ready content, state that the saved draft was not changed, and ask only whether a separate replacement draft should be created.

Creating or updating a draft never authorizes sending it. Do not call an email-send or draft-send action from this Skill. A later explicit send request must be handled as a separate Gmail action after the user has reviewed the saved draft or clearly asked to send that exact draft.

If Gmail is unavailable, the recipient cannot be resolved safely, or the thread is ambiguous, return the best paste-ready subject and body, state that it was not saved, and identify only the one missing fact or connection needed to save it.

Completion requires a returned Gmail draft identifier and matching recipient and subject evidence. Report the saved draft concisely with `To`, subject, and draft ID; do not claim success from prose generation alone.

## Output format

Unless requested otherwise, return:

1. a subject line;
2. a ready-to-send body;
3. no commentary after the draft unless an ambiguity could create commercial or interpersonal risk.

Use `〇〇様` and omit the signature when names or signature details are not provided. Do not invent recipients, dates, prices, deadlines, or commitments.

When a Gmail draft was saved, return instead:

1. `Gmail下書きに保存しました`;
2. `To` and subject;
3. the returned draft ID;
4. only material unresolved details, if any.

## Revision behavior

When the user says only `柔らかく`, `固い`, or similar:

- retain the previous version's content and structure;
- change only tone, rhythm, and phrasing;
- do not shorten away explanations, scope, or exclusions;
- move one level softer per revision instead of jumping to casual language.

If the user says `内容は前の方がいい`, restore the earlier substantive content before adjusting tone.
