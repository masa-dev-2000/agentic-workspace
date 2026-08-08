from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import yaml

from skill_history import history, state_root


ROOT = Path(__file__).resolve().parents[2]

STAGES = {
    1: ("相談する", "やりたいことを聞き、進め方を決める"),
    2: ("調べる", "情報を集め、何が本当かを整理する"),
    3: ("作る", "資料・アプリ・文章などを形にする"),
    4: ("確かめる", "間違い・危険・使いにくさを確認する"),
    5: ("覚えて改善する", "結果や失敗を記録し、次を良くする"),
}

CATEGORIES = {
    "understand": ("理解・判断", "調べ、整理し、判断材料を作る"),
    "manage": ("計画・統括", "仕事を分け、順番と担当を整える"),
    "create": ("作成・変換", "文書・アプリ・素材を形にする"),
    "verify": ("検証・品質保証", "間違い・危険・品質を確かめる"),
    "operate": ("実行・運用", "PC・ファイル・外部環境を操作する"),
    "learn": ("記録・学習", "結果を残し、次の改善へつなげる"),
    "clone": ("個人知能・判断継承", "個人の判断基準を記録・再現する"),
}

PLAIN_NAMES = {
    "agent-team-orchestrator": "AIチームをまとめる",
    "anonymize-files-locally": "秘密を隠して安全なファイルにする",
    "archive-deliverables": "完成した成果物を整理する",
    "audit-ocr-semantic-structure": "読み取った表や文書のズレを見つける",
    "build-client-decision-decks": "お客さまが決めやすい資料を作る",
    "build-complete-app": "動くアプリを作る",
    "build-decision-ready-materials": "判断に使える資料を作る",
    "build-dependency-roadmap": "作業の順番を地図にする",
    "draft-japanese-contracts": "日本語の契約書を作る",
    "draft-natural-japanese-email": "自然な日本語メールを作る",
    "failure-learning": "失敗から学ぶ",
    "failure-loop-guard": "同じ失敗の繰り返しを止める",
    "feedback-learning": "感想から改善点を見つける",
    "gan": "複数の視点で厳しく確かめる",
    "govern-repository-layout": "フォルダ構成を点検する",
    "harvest-components": "再利用できる部品を集める",
    "hatch-pet": "動くキャラクターを作る",
    "implement-v2-work": "決めた改善を実装する",
    "name-work-sessions": "作業に分かりやすい名前を付ける",
    "open-deliverable": "完成したファイルを開く",
    "persona-journey": "使う人の立場で試す",
    "phase-gate-manager": "次の段階へ進めるか決める",
    "powershell-preflight": "Windows用スクリプトを事前点検する",
    "quality-protocol": "品質の基準をそろえる",
    "remembering-conversations": "過去の会話を思い出す",
    "rerun-changed-e2e": "変えた部分だけ動作確認する",
    "research-alignment-deep-research": "目的を決めて深く調べる",
    "run-ai-consulting-sales": "AI相談の商談を進める",
    "skill-idea-inbox": "新しい機能のアイデアをためる",
    "skill-telemetry": "機能の利用記録を集める",
    "ui-ux-pro-max": "画面を使いやすく設計する",
    "windows-performance-triage": "Windowsが重い原因を調べる",
}


def flow_stage(skill_id: str) -> int:
    text = skill_id.lower()
    if any(word in text for word in ("learning", "telemetry", "remember", "ledger", "distill", "normalizer", "idea-inbox")):
        return 5
    if any(word in text for word in ("audit", "eval", "verify", "quality", "guard", "rerun", "preflight", "gan", "progress-verifier")):
        return 4
    if any(word in text for word in ("build-", "draft-", "hatch", "harvest", "implement", "anonymize", "archive", "open-deliverable")):
        return 3
    if any(word in text for word in ("research", "observer", "project-check", "windows-performance", "ocr")):
        return 2
    return 1


def plain_name(skill_id: str) -> str:
    if skill_id in PLAIN_NAMES:
        return PLAIN_NAMES[skill_id]
    tail = skill_id.split(":")[-1]
    replacements = {
        "project-orchestrator": "プロジェクト全体を進める",
        "evidence-project-planner": "根拠をもとに計画を作る",
        "human-task-requester": "人にお願いする作業をまとめる",
        "pm-settings": "プロジェクト管理の設定を変える",
        "progress-verifier": "作業が本当に終わったか確かめる",
        "project-check": "プロジェクトの準備を点検する",
        "project-normalizer": "プロジェクト資料を整える",
        "project-observer": "プロジェクトの変化を見守る",
        "reminder-escalator": "必要な人へ忘れず知らせる",
        "task-router": "仕事を誰に任せるか決める",
        "ask-my-clone": "自分ならどう考えるか予測する",
        "build-my-clone": "自分の判断モデルを育てる",
        "clone-audit": "判断モデルの記録を点検する",
        "clone-distill": "自分の判断基準を整理する",
        "clone-eval": "判断モデルの正しさを確かめる",
        "clone-interview": "質問で判断基準を集める",
        "clone-ledger": "判断の材料を記録する",
    }
    return replacements.get(tail, tail.replace("-", " "))


def plain_summary(skill_id: str) -> str:
    name = plain_name(skill_id)
    stage_name, stage_help = STAGES[flow_stage(skill_id)]
    return f"{name}ための機能です。『{stage_name}』の段階で使います。{stage_help}。"


def primary_category(skill_id: str, capability: str) -> str:
    text = f"{skill_id} {capability}".lower()
    if "clone." in text or "self-clone:" in text:
        return "clone"
    if any(word in text for word in ("learning.", "telemetry", "memory.", "skill-idea")):
        return "learn"
    if any(word in text for word in ("quality.", "review.", "audit", "verify", "preflight", "failure.guard")):
        return "verify"
    if any(word in text for word in ("project.", "task.", "orchestr", "phase.")):
        return "manage"
    if any(word in text for word in ("operations.", "open-local", "session.name")):
        return "operate"
    if any(word in text for word in ("document.", "product.", "material.", "creative.", "reuse.", "artifact.deliverable.archive", "communication.")):
        return "create"
    return "understand"


def enrich_history(events: list[dict]) -> list[dict]:
    previous: dict[str, str] = {}
    enriched = []
    for index, event in enumerate(events):
        current = {
            item.get("path", ""): item.get("sha256", "")
            for item in event.get("file_manifest", [])
        }
        changed = sorted(
            path
            for path in set(previous) | set(current)
            if previous.get(path) != current.get(path)
        )
        copy = dict(event)
        copy["change_kind"] = "記録開始" if index == 0 else "改善"
        copy["changed_files"] = changed
        copy["change_summary"] = (
            "この時点から変更内容を追跡できます"
            if index == 0
            else f"{len(changed)}個の構成ファイルが変わりました"
        )
        enriched.append(copy)
        previous = current
    return enriched


def load_data(registry: Path) -> dict:
    document = yaml.safe_load(registry.read_text(encoding="utf-8-sig"))
    skills = []
    capability_owner = {}
    for row in document.get("skills", []):
        capability_owner[row["capability"]] = row["key"]
        skills.append(
            {
                "id": row["key"],
                "plain_name": plain_name(row["key"]),
                "plain_summary": plain_summary(row["key"]),
                "flow_stage": flow_stage(row["key"]),
                "category": primary_category(row["key"], row["capability"]),
                "capability": row["capability"],
                "source": row.get("source", ""),
                "version": str(row.get("version", "")),
                "maturity": row.get("maturity", ""),
                "responsibility": row.get("responsibility", ""),
                "dependencies": row.get("dependencies", {}).get("capabilities", []),
                "authority": row.get("authority", {}).get("effects", []),
                "outputs": [
                    item.get("name", "")
                    for item in row.get("interfaces", {}).get("outputs", [])
                ],
            }
        )
    edges = []
    for skill in skills:
        for capability in skill["dependencies"]:
            owner = capability_owner.get(capability)
            if owner:
                edges.append(
                    {
                        "from": owner,
                        "to": skill["id"],
                        "capability": capability,
                        "evidence": "canonical-registry",
                    }
                )
    histories = {
        skill["id"]: enrich_history(history(skill["id"], state_root()))
        for skill in skills
    }
    return {
        "skills": skills,
        "edges": edges,
        "histories": histories,
        "summary": {
            "skills": len(skills),
            "edges": len(edges),
            "with_history": sum(bool(events) for events in histories.values()),
        },
    }


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skill Evolution Atlas</title>
<style>
:root{color-scheme:light;--bg:#f5f7f8;--panel:#fff;--line:#9aa7b1;--text:#17222b;--muted:#455764;--green:#167344;--blue:#075985;--gold:#a65d00;--purple:#6d4bb4}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:"BIZ UDPGothic","Yu Gothic UI","Meiryo",sans-serif}
button,input,select{font:inherit}.app{height:100dvh;display:grid;grid-template-rows:76px 1fr}
header{display:flex;align-items:center;gap:24px;padding:0 26px;border-bottom:2px solid #cbd5dc;background:#fff}
.brand{font-size:21px;font-weight:800;letter-spacing:.02em;line-height:1.25}.brand small{display:block;color:var(--muted);font-size:14px;font-weight:600;margin-top:3px}
.tabs{display:flex;gap:10px;margin-left:auto}.tab{min-height:48px;border:2px solid #bcc8d0;background:#fff;color:#344651;padding:10px 18px;border-radius:10px;cursor:pointer;font-size:16px;font-weight:700}.tab:hover,.tab:focus{border-color:var(--blue);outline:3px solid #bae6fd}.tab[aria-selected=true]{color:#fff;background:#075985;border-color:#075985}
.stats{display:none}
main{min-height:0;display:grid;grid-template-columns:1fr 360px}main.flowMode,main.evolutionMode{grid-template-columns:1fr}main.flowMode aside,main.evolutionMode aside{display:none}.workspace{min-width:0;min-height:0;position:relative}.toolbar{position:absolute;z-index:4;top:14px;left:18px;right:18px;display:flex;gap:10px;pointer-events:none}
.workspace.flowMode .toolbar,.workspace.evolutionMode .toolbar{display:none}.toolbar>*{pointer-events:auto}.search{width:min(430px,48vw);min-height:50px;background:#fff;border:2px solid #8796a1;color:var(--text);padding:11px 15px;border-radius:10px;font-size:17px}.filter{min-height:50px;background:#fff;border:2px solid #8796a1;color:var(--text);padding:9px 12px;border-radius:10px;font-size:16px}
.stage{width:100%;height:100%;overflow:hidden}.stage svg{width:100%;height:100%;display:block}.edge{stroke:#64748b;stroke-width:1.8;opacity:.65}.edge.hot{stroke:var(--gold);stroke-dasharray:8 5;opacity:.95}.node{cursor:pointer}.node rect{fill:#fff;stroke:#71808b;stroke-width:2}.node:hover rect,.node:focus rect{stroke:var(--blue);stroke-width:3}.node.selected rect{stroke:var(--green);stroke-width:3}.node text{fill:var(--text);font-size:12px}.node .meta{fill:var(--muted);font-size:10px}.badge{fill:#dbeafe}.badgeText{fill:#075985!important;font-size:9px!important}
aside{min-width:0;border-left:2px solid #cbd5dc;background:var(--panel);display:grid;grid-template-rows:auto 1fr}.asideHead{padding:22px;border-bottom:2px solid #dbe2e7;position:relative}.asideHead h2{font-size:21px;line-height:1.4;margin:0 42px 7px 0}.asideHead p{margin:0;color:var(--muted);font-size:15px;line-height:1.5}.closeDetail{position:absolute;right:12px;top:12px;width:48px;height:48px;border:2px solid #7b8993;border-radius:9px;background:#fff;color:#17222b;cursor:pointer;display:block;font-size:28px}.details{padding:22px;overflow:auto;scrollbar-width:thin;scrollbar-color:#7b8993 transparent}.section{margin-bottom:24px}.label{color:#344651;font-size:14px;font-weight:800;margin-bottom:8px}.value{font-size:17px;line-height:1.75}.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{font-size:13px;padding:7px 9px;border:1px solid #7b8993;border-radius:999px;color:#344651}.empty{color:var(--muted);font-size:17px;line-height:1.7}
.timeline{height:100%;padding:86px 24px 20px;overflow:auto;scrollbar-width:thin}.timelineTrack{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px;align-items:start}.event{background:#fff;border:2px solid #81909a;border-radius:12px;padding:18px;min-height:168px}.event.observed{border-color:#167344}.event.inferred{border-style:dashed}.eventTop{display:flex;justify-content:space-between;gap:10px;font-size:14px;color:var(--muted)}.event h3{font-size:19px;margin:16px 0 9px}.event p{font-size:16px;color:#344651;line-height:1.6;margin:5px 0}.legend{position:absolute;bottom:14px;left:16px;background:#fffffff2;border:2px solid #9aa7b1;padding:10px 12px;border-radius:9px;color:#344651;font-size:13px}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 5px 0 12px}.dot:first-child{margin-left:0}
.overview{height:100%;padding:28px 26px 20px;overflow:auto;scrollbar-width:thin}.overviewLead{max-width:920px;margin:0 auto 24px;text-align:center}.overviewLead h1{font-size:32px;line-height:1.35;margin:0 0 10px}.overviewLead p{color:#344651;line-height:1.65;margin:0;font-size:19px}.groupGrid{display:grid;grid-template-columns:repeat(5,minmax(165px,1fr));gap:16px;max-width:1260px;margin:0 auto}.groupCard{min-height:300px;text-align:left;background:#fff;border:3px solid #84929c;border-radius:16px;color:var(--text);padding:20px;cursor:pointer;position:relative;box-shadow:0 3px 0 #d5dce1}.groupCard:not(:last-child)::after{content:"→";position:absolute;right:-17px;top:44%;z-index:2;color:#8a4d00;font-size:28px;font-weight:900}.groupCard:hover,.groupCard:focus{border-color:#075985;outline:4px solid #bae6fd}.groupTop{display:flex;justify-content:space-between;gap:10px;align-items:center}.stepNo{display:grid;place-items:center;width:52px;height:52px;border-radius:50%;background:#075985;color:#fff;font-size:26px;font-weight:900}.groupCard h2{font-size:24px;line-height:1.35;margin:17px 0 0}.groupCard p{font-size:17px;line-height:1.7;color:#263842;margin:14px 0}.groupMeta{font-size:15px;color:#344651;line-height:1.65;border-top:2px solid #e2e8ec;padding-top:12px}.cardAction{display:block;margin-top:10px;color:#075985;font-size:16px;font-weight:900;text-decoration:underline}.node.dim{opacity:.18}.edge.dim{opacity:.06}.relationList{display:grid;gap:10px}.relation{font-size:15px;border-left:4px solid #64748b;padding-left:10px}.relation b{display:block;color:#263842}.relation span{color:var(--muted)}details.tech{border-top:2px solid #cbd5dc;padding-top:16px}details.tech summary{cursor:pointer;color:#075985;font-size:16px;font-weight:800;min-height:44px}
.overview::-webkit-scrollbar,.timeline::-webkit-scrollbar,.details::-webkit-scrollbar{width:6px}.overview::-webkit-scrollbar-thumb,.timeline::-webkit-scrollbar-thumb,.details::-webkit-scrollbar-thumb{background:#334155;border-radius:8px}.overview::-webkit-scrollbar-track,.timeline::-webkit-scrollbar-track,.details::-webkit-scrollbar-track{background:transparent}
.evolution{height:100%;padding:22px 24px;overflow:auto;scrollbar-width:thin}.evoIntro{display:flex;align-items:end;justify-content:space-between;gap:20px;margin:0 auto 18px;max-width:1400px}.evoIntro h1{font-size:28px;margin:0 0 5px}.evoIntro p{font-size:16px;color:#455764;margin:0;line-height:1.5}.evoStats{display:flex;gap:10px}.evoStat{min-width:112px;border:2px solid #b7c2c9;border-radius:10px;background:#fff;padding:9px 12px}.evoStat b{display:block;font-size:22px;color:#075985}.evoStat span{font-size:12px;color:#455764}.evoLegend{max-width:1400px;margin:0 auto 12px;display:flex;gap:18px;font-size:13px;color:#455764}.evoLegend b{color:#17222b}.lanes{max-width:1400px;margin:0 auto;display:grid;gap:8px}.lane{display:grid;grid-template-columns:190px minmax(0,1fr);min-height:84px;border:2px solid #c7d0d6;border-radius:12px;background:#fff;overflow:hidden}.laneHead{padding:13px 15px;background:#edf3f6;border-right:2px solid #c7d0d6}.laneHead h2{font-size:16px;margin:0 0 5px}.laneHead p{font-size:12px;line-height:1.45;color:#455764;margin:0}.laneBody{display:flex;align-items:center;gap:9px;padding:10px 12px;overflow:auto;scrollbar-width:thin}.historyEvent,.currentSkill{flex:0 0 auto;border-radius:9px;min-height:58px;text-align:left;cursor:pointer}.historyEvent{width:220px;border:3px solid #167344;background:#f0fdf4;padding:9px 11px;color:#173524}.historyEvent.inferred{border-style:dashed;border-color:#a65d00;background:#fff8eb}.historyEvent .eventKind{font-size:12px;font-weight:900;color:#166534}.historyEvent strong{display:block;font-size:15px;margin:3px 0}.historyEvent small{display:block;font-size:12px;color:#455764}.currentSkill{width:170px;border:2px solid #aab5bc;background:#f8fafb;padding:9px 11px;color:#344651}.currentSkill strong{display:block;font-size:14px;line-height:1.35}.currentSkill small{display:block;font-size:11px;color:#667782;margin-top:4px}.historyEvent:hover,.historyEvent:focus,.currentSkill:hover,.currentSkill:focus{outline:3px solid #bae6fd;border-color:#075985}.laneEmpty{font-size:13px;color:#6b7881}.knowledgeGap{border:2px dashed #a65d00;background:#fff8eb;color:#684000;border-radius:10px;padding:10px 14px;font-size:14px;line-height:1.5;max-width:1400px;margin:12px auto 0}
@media(max-width:900px){.evoIntro{align-items:start;flex-direction:column}.evoStats{width:100%}.evoStat{flex:1;min-width:0}.lane{grid-template-columns:145px minmax(0,1fr)}.laneHead{padding:11px}.evolution{padding:16px 12px}}
@media(max-width:1100px){.groupGrid{grid-template-columns:1fr}.groupCard{min-height:190px}.groupCard:not(:last-child)::after{content:"↓";right:50%;top:auto;bottom:-25px}.overview{padding-left:18px;padding-right:18px}}
@media(max-width:760px){.app{grid-template-rows:118px 1fr}header{padding:8px 10px;gap:8px;display:grid;grid-template-rows:38px 56px}.brand{font-size:18px}.brand small{display:none}.tabs{margin:0;display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.tab{min-height:52px;padding:7px 5px;font-size:14px;line-height:1.25}main{grid-template-columns:1fr}aside{position:absolute;z-index:8;right:0;top:118px;bottom:0;width:100vw;transform:translateX(100%);transition:transform .2s}aside.open{transform:none}.closeDetail{display:block}.toolbar{left:10px;right:10px;display:grid;grid-template-columns:minmax(0,1fr)}.search{width:auto;min-width:0}.filter{width:100%}.overviewLead h1{font-size:25px}.overviewLead p{font-size:17px}.groupCard h2{font-size:23px}.groupCard p{font-size:17px}.groupMeta{font-size:15px}.evolution{padding:12px 8px}.evoIntro h1{font-size:23px}.evoIntro p{font-size:15px}.evoStats{gap:5px}.evoStat{padding:7px}.evoStat b{font-size:18px}.evoStat span{font-size:10px}.evoLegend{font-size:12px;flex-wrap:wrap;gap:8px}.lane{grid-template-columns:112px minmax(0,1fr)}.laneHead h2{font-size:14px}.laneHead p{display:none}.historyEvent{width:205px}.currentSkill{width:155px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style></head>
<body><div class="app">
<header><div class="brand">Skill知見進化マップ<small>問題から生まれ、検証され、受け継がれた知見</small></div>
<nav class="tabs" aria-label="表示切替"><button class="tab" id="historyTab" aria-selected="true">知見の歴史</button><button class="tab" id="overviewTab" aria-selected="false">現在のSkill</button><button class="tab" id="networkTab" aria-selected="false">依存関係</button></nav>
<div class="stats"><span><b id="skillCount"></b> Skills</span><span><b id="edgeCount"></b> Connections</span><span><b id="historyCount"></b> Tracked</span></div></header>
<main id="main" class="evolutionMode"><section class="workspace evolutionMode" id="workspace"><div class="toolbar"><input id="search" class="search" type="search" placeholder="何をしたいですか？" aria-label="機能検索"><select id="groupFilter" class="filter" aria-label="段階フィルター"><option value="">すべての段階</option></select></div><div id="stage" class="stage"></div><div id="legend" class="legend" hidden><span class="dot" style="background:#a65d00"></span>先に必要な機能 <span class="dot" style="background:#167344"></span>改善記録あり</div></section>
<aside id="aside"><div class="asideHead"><button id="closeDetail" class="closeDetail" aria-label="詳細を閉じる">×</button><h2 id="detailTitle">機能を選ぶ</h2><p id="detailSub">カードを押すと説明が出ます</p></div><div id="details" class="details"><p class="empty">気になる機能を選ぶと、「何ができるか」をやさしい言葉で確認できます。</p></div></aside></main></div>
<script>const DATA=""" + payload + """;
const $=s=>document.querySelector(s);let selected=null,mode="history";
const stages={1:["相談する","やりたいことを聞き、進め方を決めます"],2:["調べる","情報を集め、何が本当かを整理します"],3:["作る","資料・アプリ・文章などを形にします"],4:["確かめる","間違い・危険・使いにくさを確認します"],5:["覚えて改善する","結果や失敗を記録し、次を良くします"]};
const categories={understand:["理解・判断","調べ、整理し、判断材料を作る"],manage:["計画・統括","仕事を分け、順番と担当を整える"],create:["作成・変換","文書・アプリ・素材を形にする"],verify:["検証・品質保証","間違い・危険・品質を確かめる"],operate:["実行・運用","PC・ファイル・外部環境を操作する"],learn:["記録・学習","結果を残し、次の改善へつなげる"],clone:["個人知能・判断継承","個人の判断基準を記録・再現する"]};
const groupOf=s=>s.id.includes(":")?"Project Manager":s.id.includes("telemetry")||s.id.includes("learning")||s.id.includes("guard")?"Governance":s.id.includes("build-")||s.id.includes("draft-")||s.id.includes("presentation")||s.id.includes("document")?"Creation":s.source==="plugin"?"Plugin":"Operations";
const groupHelp={"Operations":"実行・運用・品質を支える基盤","Creation":"資料・アプリ・成果物を作る能力","Governance":"観測・学習・安全性を管理する能力","Project Manager":"計画・割当・検証をつなぐ管理能力","Plugin":"外部サービスや専用環境との接続"};
const groupTitle={"Operations":"運用・品質","Creation":"制作","Governance":"統制・学習","Project Manager":"プロジェクト管理","Plugin":"外部連携"};
const groups=Object.keys(stages);groups.forEach(g=>$("#groupFilter").insertAdjacentHTML("beforeend",`<option value="${g}">${g}. ${stages[g][0]}</option>`));
$("#skillCount").textContent=DATA.summary.skills;$("#edgeCount").textContent=DATA.summary.edges;$("#historyCount").textContent=DATA.summary.with_history;
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function filtered(){const q=$("#search").value.toLowerCase(),g=$("#groupFilter").value;return DATA.skills.filter(s=>(!g||String(s.flow_stage)===g)&&(!q||`${s.plain_name} ${s.plain_summary} ${s.id} ${s.capability}`.toLowerCase().includes(q)))}
function layout(skills){const by={};skills.forEach(s=>(by[groupOf(s)]??=[]).push(s));const result={};Object.entries(by).forEach(([g,rows],gi)=>{const baseX=55+(gi%3)*480,baseY=115+Math.floor(gi/3)*340,cols=Math.min(3,Math.ceil(Math.sqrt(rows.length)));rows.forEach((s,i)=>{result[s.id]={x:baseX+(i%cols)*145,y:baseY+Math.floor(i/cols)*72,g}})});return result}
function renderOverview(){const visible=filtered();const cards=groups.map(g=>{const rows=visible.filter(s=>String(s.flow_stage)===g);return `<button class="groupCard" data-group="${g}"><div class="groupTop"><span class="stepNo">${g}</span></div><h2>${esc(stages[g][0])}</h2><p>${esc(stages[g][1])}</p><div class="groupMeta">たとえば<br><strong>${rows[0]?esc(rows[0].plain_name):"該当する機能はありません"}</strong><span class="cardAction">押して、この段階を見る</span></div></button>`}).join("");$("#stage").innerHTML=`<div class="overview"><div class="overviewLead"><h1>AIは、この順番でお手伝いします</h1><p>番号を1から順に見るだけで大丈夫です。気になるところは、大きなカードを押してください。</p></div><div class="groupGrid">${cards}</div></div>`;document.querySelectorAll(".groupCard").forEach(card=>{const open=()=>{$("#groupFilter").value=card.dataset.group;setMode("network")};card.onclick=open;card.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open()}}})}
function renderNetwork(){const skills=filtered(),pos=layout(skills),ids=new Set(skills.map(s=>s.id)),related=new Set([selected]);DATA.edges.forEach(e=>{if(e.from===selected)related.add(e.to);if(e.to===selected)related.add(e.from)});const focus=selected&&ids.has(selected);const edges=DATA.edges.filter(e=>ids.has(e.from)&&ids.has(e.to));let svg=`<svg viewBox="0 0 1500 820" role="img" aria-label="機能のつながり"><defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#64748b"/></marker></defs>`;edges.forEach(e=>{const a=pos[e.from],b=pos[e.to],dim=focus&&e.from!==selected&&e.to!==selected;svg+=`<path class="edge hot ${dim?"dim":""}" marker-end="url(#arrow)" d="M${a.x+120},${a.y+25} C${a.x+170},${a.y+25} ${b.x-45},${b.y+25} ${b.x},${b.y+25}"><title>先に必要な機能</title></path>`});skills.forEach(s=>{const p=pos[s.id],tracked=DATA.histories[s.id]?.length,dim=focus&&!related.has(s.id),label=s.plain_name.length>12?s.plain_name.slice(0,11)+"…":s.plain_name;svg+=`<g class="node ${selected===s.id?"selected":""} ${dim?"dim":""}" data-id="${esc(s.id)}" tabindex="0" role="button" aria-label="${esc(s.plain_name)}"><rect x="${p.x}" y="${p.y}" width="126" height="54" rx="8"/><text x="${p.x+9}" y="${p.y+21}">${esc(label)}</text><text class="meta" x="${p.x+9}" y="${p.y+40}">${s.flow_stage}. ${esc(stages[s.flow_stage][0])}</text>${tracked?`<circle cx="${p.x+114}" cy="${p.y+12}" r="5" fill="#22c55e"/>`:""}</g>`});svg+="</svg>";$("#stage").innerHTML=svg;document.querySelectorAll(".node").forEach(n=>{const pick=()=>select(n.dataset.id);n.onclick=pick;n.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();pick()}}})}
function revealAside(){if(mode==="history"){$("#main").classList.remove("evolutionMode");$("#workspace").classList.remove("evolutionMode")}$("#aside").classList.add("open")}
function select(id){selected=id;const s=DATA.skills.find(x=>x.id===id);$("#detailTitle").textContent=s.plain_name;$("#detailSub").textContent=`${categories[s.category][0]}のSkill`;const incoming=DATA.edges.filter(e=>e.to===id),outgoing=DATA.edges.filter(e=>e.from===id),events=DATA.histories[id]||[],rels=[...incoming.map(e=>({label:"先に使う",name:DATA.skills.find(x=>x.id===e.from)?.plain_name||e.from,cap:e.capability})),...outgoing.map(e=>({label:"このあと使う",name:DATA.skills.find(x=>x.id===e.to)?.plain_name||e.to,cap:e.capability}))];$("#details").innerHTML=`<div class="section"><div class="label">現在できること</div><div class="value">${esc(s.plain_summary)}</div></div><div class="section"><div class="label">確認できた進化</div><div class="value">${events.length?`${events.length}件の変更記録があります`:"現在の状態だけ確認できます。誕生・改善の理由は未記録です。"}</div></div><div class="section"><div class="label">現在の利用関係</div><div class="relationList">${rels.map(r=>`<div class="relation"><b>${esc(r.label)}：${esc(r.name)}</b></div>`).join("")||'<span class="empty">直接つながる機能はありません</span>'}</div></div><details class="tech"><summary>くわしい技術情報</summary><div class="section"><div class="label">正式なSkill名</div><div class="value">${esc(s.id)}</div></div><div class="section"><div class="label">本来の役割</div><div class="value">${esc(s.responsibility)}</div></div><div class="chips"><span class="chip">${esc(s.maturity)}</span><span class="chip">v${esc(s.version)}</span></div></details>`;revealAside();if(mode==="network")renderNetwork()}
function selectEvent(skillId,eventId){const s=DATA.skills.find(x=>x.id===skillId),e=(DATA.histories[skillId]||[]).find(x=>x.event_id===eventId);$("#detailTitle").textContent=`${s.plain_name}：${e.change_kind}`;$("#detailSub").textContent=`${e.effective_date} · ${e.provenance==="observed"?"確認済み":"資料から復元"}`;$("#details").innerHTML=`<div class="section"><div class="label">何が変わった？</div><div class="value">${esc(e.change_summary)}</div></div><div class="section"><div class="label">きっかけ</div><div class="value">${e.change_kind==="記録開始"?"この時点から正確な変更追跡を開始しました。":"変更理由は現在の履歴台帳に記録されていません。"}</div></div><div class="section"><div class="label">変わった場所</div><div class="chips">${(e.changed_files||[]).slice(0,8).map(x=>`<span class="chip">${esc(x)}</span>`).join("")||"不明"}</div></div><div class="section"><div class="label">得られた知見</div><div class="value">未記録です。今後の改善では、問題・学び・検証結果を一緒に残します。</div></div><details class="tech"><summary>証拠情報</summary><div class="value">記録種別：${esc(e.provenance)}<br>バージョン：${esc(e.skill_version||"不明")}</div></details>`;revealAside()}
function renderEvolution(){const allEvents=DATA.skills.flatMap(s=>(DATA.histories[s.id]||[]).map(e=>({...e,skill:s.id,plain:s.plain_name,category:s.category}))).sort((a,b)=>String(a.observed_at||a.effective_date).localeCompare(String(b.observed_at||b.effective_date)));const tracked=new Set(allEvents.map(e=>e.skill));const laneHtml=Object.entries(categories).map(([key,info])=>{const laneSkills=DATA.skills.filter(s=>s.category===key),events=allEvents.filter(e=>e.category===key);const content=[...events.map(e=>`<button class="historyEvent ${esc(e.provenance)}" data-skill="${esc(e.skill)}" data-event="${esc(e.event_id)}"><span class="eventKind">${esc(e.change_kind)} · ${e.provenance==="observed"?"確認済み":"復元"}</span><strong>${esc(e.plain)}</strong><small>${esc(e.change_summary)} · ${esc(e.effective_date)}</small></button>`),...laneSkills.filter(s=>!tracked.has(s.id)).map(s=>`<button class="currentSkill" data-skill="${esc(s.id)}"><strong>${esc(s.plain_name)}</strong><small>現在だけ確認・歴史未記録</small></button>`)].join("");return `<section class="lane"><div class="laneHead"><h2>${esc(info[0])}</h2><p>${esc(info[1])}</p></div><div class="laneBody">${content||'<span class="laneEmpty">まだSkillがありません</span>'}</div></section>`}).join("");$("#stage").innerHTML=`<div class="evolution"><div class="evoIntro"><div><h1>Skillは、問題と学びから育ってきました</h1><p>確認できる事実と、まだ分からない過去を分けて表示します。各レーンは横方向が変化の順番です。</p></div><div class="evoStats"><div class="evoStat"><b>${allEvents.length}</b><span>確認・復元した変更</span></div><div class="evoStat"><b>${tracked.size}</b><span>歴史を追えるSkill</span></div><div class="evoStat"><b>${DATA.skills.length-tracked.size}</b><span>歴史が未記録</span></div></div></div><div class="evoLegend"><span><b>緑</b> 証拠がある変更</span><span><b>橙の点線</b> 残った資料から復元</span><span><b>灰色</b> 現在の状態のみ</span></div><div class="lanes">${laneHtml}</div><div class="knowledgeGap"><b>現時点の限界：</b>過去の「なぜ変えたか」「何を学んだか」は多くが未記録です。推測で埋めず、今後の改善から因果と検証結果を蓄積します。</div></div>`;document.querySelectorAll(".historyEvent").forEach(n=>n.onclick=()=>selectEvent(n.dataset.skill,n.dataset.event));document.querySelectorAll(".currentSkill").forEach(n=>n.onclick=()=>select(n.dataset.skill))}
function setMode(next){mode=next;const flow=next==="overview",evolution=next==="history";$("#workspace").classList.toggle("flowMode",flow);$("#main").classList.toggle("flowMode",flow);$("#workspace").classList.toggle("evolutionMode",evolution);$("#main").classList.toggle("evolutionMode",evolution);if(flow||evolution){$("#aside").classList.remove("open");$("#groupFilter").value="";$("#search").value=""}$("#overviewTab").setAttribute("aria-selected",flow);$("#networkTab").setAttribute("aria-selected",next==="network");$("#historyTab").setAttribute("aria-selected",evolution);$("#legend").hidden=next!=="network";flow?renderOverview():next==="network"?renderNetwork():renderEvolution()}
$("#overviewTab").onclick=()=>setMode("overview");$("#networkTab").onclick=()=>setMode("network");$("#historyTab").onclick=()=>setMode("history");$("#closeDetail").onclick=()=>{if(mode==="history"){$("#main").classList.add("evolutionMode");$("#workspace").classList.add("evolutionMode")}$("#aside").classList.remove("open")};$("#search").oninput=()=>mode==="overview"?renderOverview():mode==="network"?renderNetwork():renderEvolution();$("#groupFilter").onchange=$("#search").oninput;renderEvolution();
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=ROOT / "skill-registry.yaml")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = load_data(args.registry.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(data), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {"valid": True, "output": str(output), **data["summary"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
