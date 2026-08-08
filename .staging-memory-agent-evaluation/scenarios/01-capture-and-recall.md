# Scenario 01: Capture and recall

## 入力

`fixtures/wish-list.jsonl` の4件を登録する。

## 質問

- 「人間の判断が必要な項目はどれか」
- 「Command Centerとモデル割当に関係する記憶を出せるか」
- 「各項目のnext_actionを失わずに返せるか」

## 合格条件

- 4件が識別可能
- title、priority、status、next_actionが保持される
- キーワード一致しない関連検索でも、関係する項目を返せる
- 出典または保存ファイルを確認できる

