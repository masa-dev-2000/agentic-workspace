教材作成フェーズ。カリキュラムに基づいてスライド・ハンドアウト・演習問題を作成する。

引数: {講習名} [モジュール番号] - 特定モジュールのみ作成する場合は番号指定
例:
  /training-materials "AI基礎講座"
  /training-materials "AI基礎講座" 3

## 前提条件

`.training/{講習名}/curriculum.md` が存在すること（カリキュラム設計完了済み）。
存在しない場合は「先に `/training-curriculum "{講習名}"` を実行してください」と案内して終了。

## 手順

### Step 1: カリキュラムの読み込み

`.training/{講習名}/curriculum.md` を読み込み、モジュール構成を把握する。

### Step 2: 作成対象の確認

- モジュール番号が指定されていれば、そのモジュールのみ作成
- 未指定なら全モジュール分を作成
- 既に作成済みの教材がある場合は上書きするか確認

### Step 3: ディレクトリ作成

```bash
mkdir -p ".training/{講習名}/materials"
mkdir -p ".training/{講習名}/exercises"
```

### Step 4: 教材の作成

各モジュールについて以下を作成:

#### スライド資料
- `.training/{講習名}/materials/` に配置
- `/anthropic-skills:pptx` スキルを活用して `.pptx` 形式で作成
- ファイル名: `module{番号}_{タイトル}.pptx`
- 1スライド = 1つのポイントを原則とする
- ビジュアル重視、テキストは最小限

#### ハンドアウト
- `.training/{講習名}/materials/handout.md` に配置
- 受講者が手元に残せる参照用資料
- スライドの補足情報、参考リンク、用語集を含む

#### 演習問題
- `.training/{講習名}/exercises/` に配置
- ファイル名: `exercise{番号}_{タイトル}.md`
- 各演習に以下を記載:
  - 目的
  - 手順
  - 期待される成果
  - 所要時間
  - （講師用）模範回答・ポイント

### Step 5: ステータス更新

`.training/{講習名}/plan.md` のステータスセクションを更新:
- [x] 教材作成（materials）

### Step 6: 次のステップを案内

以下を表示:
> 教材を作成しました: `.training/{講習名}/materials/`, `.training/{講習名}/exercises/`
> 内容を確認してください。
> 次のステップ: `/training-review "{講習名}"`
