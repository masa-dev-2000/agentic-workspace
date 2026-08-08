---
name: draft-japanese-contracts
description: Draft, revise, simplify, and audit concise Japanese B2B service agreements, quasi-mandate agreements, confidentiality clauses, personal-data clauses, and matching estimates. Use when creating or reviewing Japanese 業務委託契約書、準委任契約書、秘密保持契約書、NDA、見積書、契約条件、更新・支払・承認条項、署名欄, especially when the client-facing DOCX must stay short and internally consistent.
---

# Draft Japanese Contracts

Create a client-facing contract that is short, commercially usable, and internally consistent. Treat the output as a draft for professional legal review, not legal advice.

## Workflow

1. Collect the confirmed deal terms before drafting:
   - formal party names, addresses, representatives
   - service scope and explicit exclusions
   - contract type, start date, term, renewal and cancellation
   - fixed or variable fee, tax, invoicing and payment timing
   - communication and approval channel
   - data, AI, subcontracting and confidentiality requirements
   - governing law and court
2. Separate confirmed facts from assumptions. Do not silently invent missing legal or commercial terms.
3. Choose the shortest structure that preserves the material protections. Prefer 10–16 articles for a small B2B engagement.
4. Keep the estimate invoice-like: issuer, recipient, issue date, validity, line items, subtotal, tax and total. Put scope, exclusions, renewal, payment, confidentiality and liability in the contract.
5. Draft plain client-facing Japanese. Do not include developer notes, tool names, drafting instructions, “締結前必須”, or commentary about why a clause exists.
6. Preserve client-supplied mandatory wording verbatim unless the user authorizes legal normalization. Record any intentional deviation.
7. For DOCX work, also use the `documents` Skill and follow its render-and-verify workflow.
8. Run the consistency audit before delivery. Use `scripts/audit_contract.py` for deterministic text, blank-field and contradiction checks.

## Drafting rules

- Define 甲 and 乙 once in the opening sentence. Repeat “甲（委託者）／乙（受託者）” in the signature block because it identifies the signing parties; this is not a duplicate definition.
- Populate every confirmed party field. Never leave a labeled field blank when the information is already known.
- If a field is genuinely unknown, leave a restrained blank in the document and list it once in the handoff. Do not insert internal warnings into the client document.
- State renewal precisely: automatic or express renewal, renewal period, non-renewal notice deadline and permitted notice channel.
- State payment precisely: amount, tax, advance or arrears, invoice timing, due date and transfer fee.
- When the fee is fixed, do not add an hourly overage rate unless explicitly agreed.
- State approvals precisely: authorized person, channel, explicit approval language, treatment of reactions/silence, and how oral decisions are recorded.
- Abstract vendor-specific operations when requested. Put named vendors in an operational appendix only when necessary.
- Separate heavy deliverables such as Web/EC production from an advisory or operational-efficiency agreement when separately priced.
- Keep human approval for customer communications, price, inventory, delivery, publication and regulatory decisions unless the user explicitly changes the risk model.
- Do not promise connection to every external channel; state dependency on permissions, review and third-party specifications when relevant.
- Keep confidentiality mutual when both sides disclose information. Read `references/clause-patterns.md` when drafting confidentiality, prohibited-use, AI/data, renewal, payment or LINE approval clauses.
- Preserve legal hierarchy. Put a lead sentence in the numbered paragraph and its enumerated acts beneath it as （1）〜（n） subitems; do not turn the lead sentence and each subitem into consecutive main paragraphs.
- Read `references/review-checklist.md` before finalizing any document.

## Quality gates

Confirm all of the following:

- estimate and contract amounts, tax treatment and payment timing agree
- scope and exclusions agree across title, body and condition table
- renewal and cancellation language do not conflict
- no deleted constraint survives in another paragraph or table
- all mandatory client wording is present verbatim
- party names, addresses and representatives agree between opening and signature
- communication/approval fields are not blank when already decided
- no product, tool or vendor name remains when abstraction was requested
- no developer-facing comments appear
- filename clearly identifies the latest deliverable without overwriting prior versions

Run:

```powershell
python -X utf8 scripts/audit_contract.py CONTRACT.docx `
  --require "月額100,000円" `
  --forbid "月10時間" `
  --check-empty-table-values
```

Report structural checks separately from visual render checks.
