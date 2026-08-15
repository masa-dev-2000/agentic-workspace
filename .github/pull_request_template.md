## 目的

<!-- 何を直す／できるようにするPRか。関連Issueがあれば記載する。 -->

## 変更内容

<!-- 変更した仕組みと、採用した方針を簡潔に記載する。 -->

## 検証

- [ ] `python -X utf8 scripts/validate_workspace.py --no-live`
- [ ] `python -X utf8 -m unittest discover -s scripts/tests -v`
- [ ] 変更固有の動作確認を実行した

実行結果:

<!-- 件数・成功/失敗・実行できなかった理由を記載する。 -->

## 独立レビュー

- [ ] Codex `/review`で`main`との差分を確認した、または省略理由を下に記載した
- [ ] GitHub Codex Code Reviewを実行した
- [ ] P0/P1の指摘を解消した、またはオーナーが理由付きで受容した

省略・受容理由:

<!-- 該当しない場合は「なし」。 -->

## リスクとRollback

<!-- 想定される副作用、残存リスク、問題時に戻す方法を記載する。 -->

## オーナー確認

- [ ] 変更目的と実装方針を確認した
- [ ] Customer data・Credential・Ledger等が共有Repositoryへ混入していない
- [ ] Mergeしてよい
