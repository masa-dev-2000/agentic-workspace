# orchestration_plan_v1

必須トップレベル項目：

- `schema`
- `job_id`
- `goal`
- `tasks`
- `execution_mode`
- `verification`
- `plan_digest`
- `gan_review_evidence`
- `enrichment_digest`
- `review_required: true`

各`task`は次を持つ：

- `task_id`
- `objective`
- `dependencies`
- `write_scope`
- `side_effect_classes`
- `role`
- `model_provider`
- `acceptance_criteria`
- `verification`
- `retry_limit`
- `fallback`

`execution_mode`は`direct`、`handoff`、`team`のいずれか。副作用不明、検証不能、外部公開、破壊的変更、金銭・法務・権限変更は、計画段階で`require_approval`候補として明示する。

`plan_digest`は計画本体のcanonical JSONから算出するimmutable fingerprintであり、enrichment後に再計算・上書きしない。`review_required: true`をAdaptiveへ渡し、AdaptiveがGANをdispatchする。GAN verdict、packet/review refs、run status、coverage、completion guaranteeを`gan_review_evidence`へ保存する。証跡がない、No-Go、必須coverage欠落の場合はexecutionへ進めない。境界はPlanning→Adaptive→GAN→executionで固定する。
