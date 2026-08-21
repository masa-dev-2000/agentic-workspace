---
status: candidate
observed_at: 2026-08-21
source:
  - https://claude.com/blog/bringing-claude-mythos-5-to-more-defenders
---

# Capability-constrained interface pattern

## why_relevant

高能力・高リスクなモデルやツールを利用者へ直接開放せず、用途を限定したinterfaceの背後に置き、必要なartifactだけを返す設計。モデル能力そのものと、利用者に与える権限を分離できるため、cyber以外にも外部送信、破壊的操作、機密データ処理、高権限MCPへ転用できる。

## portable_pattern

- High-risk capabilityは直接prompt可能なsurfaceへ露出しない
- Purpose-built interfaceごとに許可task・対象resource・output schemaを固定する
- 利用者へ返すのはraw completionではなく、finding / patch proposal / alert / plan等の限定artifactにする
- Capability accessとexecution authorityを分離する
- 対象resourceのownership / authorizationをtrusted layerで検証する
- 高権限能力へのrouteはpolicy classifierだけに依存せずdeterministic policyも併用する
- artifactを実環境へ反映する前にhumanまたは別privileged handlerのreview gateを置く
- auditにはcapability、対象、artifact、review decisionを残し、不要なraw sensitive dataは保持しない
- low-risk surfaceへ高リスクcapabilityのaccess tokenやmodel handleを伝播させない

## possible_component

`criteria/capability-policy/` + `adapters/<provider>/capability-broker`

`User/Agent -> PurposeInterface -> CapabilityPolicy -> HighRiskModelOrTool -> TypedArtifact -> Review/Handler`

## next_action

既存のsafe-output gatewayと責務を分離し、まず `external-message.send`、`repo.destructive-write`、`security.deep-scan` の3能力で capability broker のPoCを作る。safe-output gatewayは「提案をどう安全に実行するか」、本候補は「高リスク能力を誰にどのsurfaceで見せるか」を担当する。
