from __future__ import annotations
import json, os, shutil, sys
from pathlib import Path

HOOK_NAME="adaptive-orchestrator"

def hooks_path():
    return Path(os.environ.get("CODEX_HOME", Path.home()/".codex"))/"hooks.json"

def command():
    script=Path(__file__).with_name("orchestration_hook.py").resolve()
    return 'python -X utf8 "'+str(script)+'"'

def load(path):
    if not path.exists(): return {"hooks":{}}
    return json.loads(path.read_text(encoding="utf-8-sig"))

def ours(hook):
    return HOOK_NAME in str(hook.get("command","")) or HOOK_NAME in str(hook.get("commandWindows",""))

def install(path):
    config=load(path); config.setdefault("hooks",{}); groups=config["hooks"].setdefault("UserPromptSubmit",[])
    if not any(ours(h) for g in groups for h in g.get("hooks",[])):
        groups.append({"hooks":[{"type":"command","command":command(),"commandWindows":command(),"timeout":2,"statusMessage":"Planning this request"}]})
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): shutil.copy2(path,path.with_suffix(".json.adaptive-orchestrator.bak"))
    tmp=path.with_suffix(".adaptive.tmp")
    tmp.write_text(json.dumps(config,ensure_ascii=False,indent=2)+chr(10),encoding="utf-8")
    os.replace(tmp,path)
    return {"installed":True,"hook":"UserPromptSubmit","path":str(path)}

def main():
    action=sys.argv[1] if len(sys.argv)>1 else "status"; path=hooks_path(); config=load(path)
    if action=="install": result=install(path)
    else: result={"installed":any(ours(h) for g in config.get("hooks",{}).get("UserPromptSubmit",[]) for h in g.get("hooks",[])),"path":str(path)}
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
