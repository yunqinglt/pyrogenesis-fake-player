"""Portable offline reports. No game process, web service, or MCP is consulted."""

from __future__ import annotations

import html
import json
from pathlib import Path


def _text(value):
    if value is None:
        return "未知"
    return html.escape(str(value)).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _duration(value):
    if type(value) not in (int, float):
        return "未知"
    seconds = max(0, int(value / 1000))
    return f"{seconds // 60}:{seconds % 60:02d}"


def render_markdown(report: dict) -> str:
    """Render concise Chinese observations, retaining evidence and uncertainty."""
    source, match, scope = (report.get(key, {}) for key in ("source", "match", "scope"))
    lines = ["# 存档局势分析", "", "分析范围：`offline-save-omniscient`。", "",
        "这是全知离线快照。焦点玩家是关注标记；JSON 保留全部玩家，图中可筛选。没有还原玩家迷雾，不能作为实时对战中的已知信息。",
        "对象分布图只显示保存的坐标，不代表地形、高度、通行性或领土。", "",
        f"来源：{_text(source.get('file'))}", "",
        f"SHA-256：{_text(source.get('sha256'))}", "",
        f"引擎：{_text(source.get('engine_version'))}；序列化：{_text(source.get('serialization_version'))}；"
        f"焦点玩家：{_text(scope.get('focus_player'))}。", "",
        f"地图：{_text(match.get('map') or match.get('map_id'))}；游戏时间：{_duration(match.get('game_time_ms'))}；"
        f"原局视野模式：{_text(match.get('visibility_mode'))}。", ""]
    ceasefire = match.get("ceasefire_actual")
    if isinstance(ceasefire, dict):
        remaining = ceasefire.get("remaining_ms", ceasefire.get("remainingTime"))
        lines += [f"存档停战状态：{_text(ceasefire.get('active'))}；剩余：{_duration(remaining)}。", ""]
    lines += [f"初始停战配置：{_text(match.get('ceasefire_minutes'))} 分钟；"
        f"按开局配置估计剩余 {_duration(match.get('estimated_ceasefire_remaining_ms'))}（估计不推断脚本改动）。", "",
        "## 玩家概况", "",
        "| 玩家 | 文明 / 阶段 | 人口（已用 / 可用 / 最大） | 单位 / 建筑 / 地基 | Worker / 空闲 | 食物 / 木材 / 石料 / 金属 |",
        "| --- | --- | --- | --- | --- | --- |"]
    phases = {"village": "乡村", "town": "城镇", "city": "都市"}
    for player in report.get("players", []):
        pop = player.get("population") or {}
        resources = player.get("resources") or {}
        lines.append("| " + " | ".join([
            f"{_text(player.get('id'))} · {_text(player.get('name'))}",
            f"{_text(player.get('civ'))} / {_text(phases.get(player.get('phase'), player.get('phase')))}",
            " / ".join(_text(pop.get(key)) for key in ("count", "limit", "max")),
            " / ".join(_text(player.get(key)) for key in ("units", "buildings", "foundations")),
            f"{_text(player.get('workers'))} / {len(player.get('idle_worker_ids') or [])}",
            " / ".join(_text(resources.get(key)) for key in ("food", "wood", "stone", "metal")),
        ]) + " |")
    lines += ["", "人口采用存档计数，可能含生产预留；Worker 是能力身份，与军人类别可能重叠，不能直接相加。", ""]
    for player in report.get("players", []):
        gatherers = player.get("gatherers") or {}
        research = player.get("research_queue") or []
        lines += [f"### 玩家 {_text(player.get('id'))} 的任务", "",
            "资源采集者（食 / 木 / 石 / 金）：" + " / ".join(_text(gatherers.get(key)) for key in ("food", "wood", "stone", "metal")) + "。", "",
            f"已研究科技：{len(player.get('researched_technologies') or [])} 项；"
            f"忙碌生产建筑：{_text(', '.join(map(str, player.get('busy_building_ids') or [])) or '无')}。", "",
            f"当前研究：{_text(json.dumps(research, ensure_ascii=False, separators=(',', ':')))}。", "",
            f"空闲 Worker ID：{_text(', '.join(map(str, player.get('idle_worker_ids') or [])) or '无')}。", ""]
    lines += ["## 有证据的观察", ""]
    findings = report.get("findings") or []
    if not findings:
        lines += ["当前规则没有产生额外观察；这不表示局势或操作没有问题。", ""]
    for finding in findings:
        who = "全局" if finding.get("player") is None else f"玩家 {finding['player']}"
        evidence = json.dumps(finding.get("evidence"), ensure_ascii=False, separators=(",", ":"))
        if len(evidence) > 500:
            evidence = evidence[:500] + "…（完整证据见 report.json）"
        lines += [f"- {_text(who)}：{_text(finding.get('message'))} 证据：{_text(evidence)}。"]
    lines += ["", "## 规则与限制", ""]
    audit = report.get("rules_audit") or {}
    lines += ["规则核对：" + ("符合配置。" if audit.get("matches") else "与案例规则存在差异，已保留存档实际设置。"), ""]
    for mismatch in audit.get("mismatches") or []:
        lines.append(f"- {_text(mismatch.get('rule'))}：期望 {_text(mismatch.get('expected'))}；实际 {_text(mismatch.get('actual'))}。")
    for warning in report.get("warnings") or []:
        lines.append(f"- 数据说明：{_text(warning.get('code'))}；{_text(warning.get('message') or json.dumps(warning, ensure_ascii=False))}。")
    lines += ["", "静态模板仅补充原文名称与基础规则，不代表当前科技修正后的属性。没有动作历史、未来收入测量或自动胜负判定。",
        "完整数据见 report.json；打开 map.html 可按玩家、类别、名称或 ID 检索并点选对象。", ""]
    return "\n".join(lines)


_MAP_HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>存档对象分布 · 全知离线分析</title>
<style>
:root{color-scheme:dark;font-family:system-ui,'Microsoft YaHei',sans-serif;background:#101923;color:#e4edf4}body{margin:0;padding:20px}h1{font-size:22px;margin:0 0 12px}.note{color:#c4d2de;max-width:1100px;line-height:1.7}.controls{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:18px 0}select,input,button{font:inherit;color:inherit;background:#203344;border:1px solid #65798b;border-radius:5px;padding:7px}input[type=search]{min-width:240px}button{cursor:pointer}main{display:grid;grid-template-columns:minmax(400px,1fr) minmax(270px,360px);gap:18px}svg{width:100%;height:min(74vh,900px);background:#142330;border:1px solid #63798b;touch-action:auto}svg circle{cursor:pointer;vector-effect:non-scaling-stroke}svg circle:focus{stroke:#fff;stroke-width:3;outline:0}aside{background:#172733;padding:16px;border-radius:8px;overflow:auto;max-height:74vh}h2{font-size:17px;margin-top:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace}#status{color:#c2d2df}#legend{display:flex;flex-wrap:wrap;gap:12px;margin:10px 0;font-size:13px}.swatch{display:inline-block;width:12px;height:12px;margin-right:5px;border-radius:50%}@media(max-width:800px){body{padding:12px}main{grid-template-columns:1fr}svg{height:65vh}aside{max-height:none}input[type=search]{min-width:150px}}
</style>
<h1>存档对象分布</h1>
<p class="note">全知离线快照：焦点玩家仅用于筛选，没有还原迷雾。这是对象坐标图，不是地形、高度、通行或领土地图。横轴为 x，纵轴为 z（向上增加）。默认隐藏装饰与历史迷雾影像。</p>
<p id="summary" class="note"></p>
<div class="controls">
<label>玩家 <select id="owner"><option value="all">全部玩家</option></select></label>
<label>类别 <select id="category"><option value="all">全部类别</option><option value="unit">单位</option><option value="structure">建筑与地基</option><option value="resource">资源</option><option value="other">其他</option></select></label>
<label>搜索 <input id="search" placeholder="ID、模板、名称、类别" type="search"></label>
<label><input id="extras" type="checkbox"> 显示装饰与历史影像</label>
<button id="focus" type="button">焦点玩家</button><button id="fit" type="button">缩放到结果</button><button id="reset" type="button">重置</button>
</div>
<p id="status" role="status" aria-live="polite"></p><div id="legend"></div>
<main><svg id="map" role="img" aria-label="存档对象坐标分布图"></svg><aside><h2 id="detail-title">对象详情</h2><p>点选图上对象查看快照状态；地图中没有坐标的驻军等对象仍保存在 report.json。</p><pre id="details">尚未选择对象。</pre></aside></main>
<script id="report-data" type="application/json">__REPORT_JSON__</script>
<script>
'use strict';
const report=JSON.parse(document.getElementById('report-data').textContent);
const ns='http://www.w3.org/2000/svg', byId=id=>document.getElementById(id);
const map=byId('map'), owner=byId('owner'), category=byId('category'), search=byId('search'), extras=byId('extras');
const entities=report.entities||[], players=report.players||[], playerMap=new Map(players.map(p=>[p.id,p]));
const finite=n=>typeof n==='number'&&Number.isFinite(n);
const positioned=entities.filter(e=>e.position&&finite(e.position.x)&&finite(e.position.z));
function color(id){const p=playerMap.get(id), c=p&&p.color;if(c&&['r','g','b'].every(k=>finite(c[k]))){const values=['r','g','b'].map(k=>Math.max(0,Math.min(255,c[k])));return 'rgb('+values.join(',')+')';}return id===0?'#8b9a77':id==null?'#7b8895':'hsl('+((id*137.508)%360)+',70%,65%)';}
function name(e){const n=e.name||{};return n.specific||n.generic||e.template||'未知对象';}
function kind(e){const cls=e.classes||[];return cls.includes('Structure')||e.foundation?'structure':e.resource?'resource':cls.includes('Unit')?'unit':'other';}
function ordinary(e){return e.representation!=='mirage'&&!String(e.template).startsWith('mirage|')&&!String(e.template).startsWith('actor|')&&(e.owner!=null||(e.classes||[]).length>0);}
const ownerIds=[...new Set([...players.map(p=>p.id),...positioned.map(e=>e.owner)])].sort((a,b)=>(a==null?1:b==null?-1:a-b));
for(const id of ownerIds){const option=document.createElement('option');option.value=id==null?'none':String(id);const p=playerMap.get(id);option.textContent=id==null?'无所有者':id===0?'0 · 自然':id+' · '+((p&&p.name)||'玩家');owner.append(option);const label=document.createElement('span'), swatch=document.createElement('span');swatch.className='swatch';swatch.style.backgroundColor=color(id);label.append(swatch,document.createTextNode(option.textContent));byId('legend').append(label);}
const focus=report.scope&&report.scope.focus_player, match=report.match||{};
byId('summary').textContent='地图：'+(match.map||match.map_id||'未知')+'；焦点玩家：'+focus+'；游戏时间：'+(finite(match.game_time_ms)?(match.game_time_ms/60000).toFixed(2)+' 分钟':'未知')+'。来源：'+((report.source||{}).file||'未知');
let minX=0,minZ=0,maxX=match.world_size,maxZ=match.world_size;
if(!finite(maxX)||maxX<=0){minX=Infinity;minZ=Infinity;maxX=-Infinity;maxZ=-Infinity;for(const e of positioned){minX=Math.min(minX,e.position.x);minZ=Math.min(minZ,e.position.z);maxX=Math.max(maxX,e.position.x);maxZ=Math.max(maxZ,e.position.z);}if(!positioned.length){minX=minZ=0;maxX=maxZ=1;}}
const width=Math.max(1,maxX-minX),height=Math.max(1,maxZ-minZ),pad=Math.max(width,height)*.025;
const fullView=[minX-pad,minZ-pad,width+2*pad,height+2*pad].join(' ');map.setAttribute('viewBox',fullView);
function node(tag,attrs){const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n;}
function draw(){map.replaceChildren();for(let i=0;i<=8;i++){const x=minX+width*i/8,z=minZ+height*i/8;map.append(node('line',{x1:x,x2:x,y1:minZ,y2:minZ+height,stroke:'#2b4254','stroke-width':Math.max(width,height)/1700}),node('line',{x1:minX,x2:minX+width,y1:z,y2:z,stroke:'#2b4254','stroke-width':Math.max(width,height)/1700}));}
const needle=search.value.toLocaleLowerCase(),chosen=owner.value;let count=0,outside=0;
for(const e of positioned){if(!extras.checked&&!ordinary(e))continue;if(chosen!=='all'&&(e.owner==null?'none':String(e.owner))!==chosen)continue;if(category.value!=='all'&&kind(e)!==category.value)continue;const hay=[e.id,e.template,name(e),...(e.classes||[])].join(' ').toLocaleLowerCase();if(needle&&!hay.includes(needle))continue;count++;const p=e.position;if(p.x<minX||p.x>minX+width||p.z<minZ||p.z>minZ+height)outside++;const r=(kind(e)==='structure'?3.6:kind(e)==='unit'?2.5:1.6)*Math.max(width,height)/800;const circle=node('circle',{cx:p.x,cy:minZ+height-(p.z-minZ),r,fill:color(e.owner),stroke:e.representation==='mirage'?'#e8d490':'#c7d8e4','stroke-width':.7,tabindex:0,role:'button','aria-label':name(e)+' #'+e.id});const title=node('title',{});title.textContent=name(e)+' #'+e.id+' · 玩家 '+e.owner;circle.append(title);const select=()=>{byId('detail-title').textContent=name(e)+' #'+e.id;byId('details').textContent=JSON.stringify(e,null,2);};circle.addEventListener('click',select);circle.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();select();}});map.append(circle);}
byId('status').textContent='显示 '+count+' 个对象；存档中 '+positioned.length+' 个对象有坐标，'+(entities.length-positioned.length)+' 个无地图坐标。'+(outside?'其中 '+outside+' 个坐标超出配置的地图范围。':'');}
for(const control of [owner,category,extras])control.addEventListener('change',draw);search.addEventListener('input',draw);
byId('fit').addEventListener('click',()=>{const circles=map.querySelectorAll('circle');if(!circles.length)return;let left=Infinity,top=Infinity,right=-Infinity,bottom=-Infinity;for(const c of circles){const x=Number(c.getAttribute('cx')),y=Number(c.getAttribute('cy')),r=Number(c.getAttribute('r'));left=Math.min(left,x-r);right=Math.max(right,x+r);top=Math.min(top,y-r);bottom=Math.max(bottom,y+r);}const side=Math.max(right-left,bottom-top,Math.max(width,height)/50),margin=side*.15;map.setAttribute('viewBox',[(left+right-side)/2-margin,(top+bottom-side)/2-margin,side+2*margin,side+2*margin].join(' '));});
byId('focus').addEventListener('click',()=>{owner.value=String(focus);draw();});byId('reset').addEventListener('click',()=>{owner.value='all';category.value='all';search.value='';extras.checked=false;map.setAttribute('viewBox',fullView);draw();});draw();
</script></html>
'''


def render_map(report: dict) -> str:
    """Return a self-contained interactive object map with inert embedded JSON."""
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # A script element's end tag is recognized even for application/json.
    for original, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                              ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(original, escaped)
    return _MAP_HTML.replace("__REPORT_JSON__", payload)


def validate_output_path(output: str | Path, input_path: str | Path) -> Path:
    requested = Path(output).expanduser()
    target = requested.resolve()
    source = Path(input_path).expanduser().resolve()
    if source == target or source.is_relative_to(target):
        raise ValueError("Output directory must not contain or replace the input save")
    if target.exists() or requested.is_symlink():
        raise ValueError(f"Output directory already exists; choose a new path: {target}")
    return target


def write_reports(report: dict, output: str | Path, input_path: str | Path) -> Path:
    """Create a new report directory, removing this call's files on any failure."""
    target = validate_output_path(output, input_path)
    contents = {
        "report.json": json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        "report.md": render_markdown(report),
        "map.html": render_map(report),
    }
    # Render everything before touching the output directory. Exclusive creation
    # also prevents accidentally replacing another report if a path races us.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=False)
    created = []
    try:
        for name, content in contents.items():
            path = target / name
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(path)
                stream.write(content)
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            pass
        raise
    return target
