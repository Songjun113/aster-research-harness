"""Lightweight Spec scaffolding, structural checks and derived status; stdlib only."""
import argparse, datetime, hashlib, json, re, tempfile, os
from pathlib import Path

STATES={'planned','in_progress','awaiting_user','blocked','verified','deferred'}
AC_STATES={'pending','partial','failed','verified'}
START='<!-- spec-start:start -->'; END='<!-- spec-start:end -->'

def atomic_write(path,text,expected=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if expected is not None and path.read_bytes()!=expected:raise ValueError(f'Concurrent edit detected: {path}')
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name);f.write(text.encode('utf-8'))
    try:
        if expected is not None and path.read_bytes()!=expected:raise ValueError(f'Concurrent edit detected: {path}')
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()

def managed_entry(root,filename,body):
    path=root/filename;old=path.read_bytes() if path.exists() else None
    text=old.decode('utf-8-sig') if old is not None else ''
    block=START+'\n'+body.strip()+'\n'+END
    if START in text or END in text:
        if text.count(START)!=1 or text.count(END)!=1:raise ValueError(f'Ambiguous managed markers: {path}')
        a=text.index(START);b=text.index(END)+len(END)
        if b<a:raise ValueError(f'Invalid marker order: {path}')
        updated=text[:a]+block+text[b:]
    else:updated=block+'\n\n'+text
    if updated!=text:atomic_write(path,updated,old)

def init(root,name):
    assets=Path(__file__).resolve().parent.parent/'assets';spec=root/'spec'
    for filename in ['project.md','requirements.json','tasks.md','decisions.md']:
        dest=spec/filename
        if not dest.exists():
            text=(assets/filename).read_text(encoding='utf-8')
            if filename=='requirements.json':
                d=json.loads(text);d['project']['name']=name or root.name;d['project']['updated_at']=datetime.date.today().isoformat();text=json.dumps(d,ensure_ascii=False,indent=2)+'\n'
            atomic_write(dest,text)
    managed_entry(root,'PROJECT.md','## Spec管理入口\n\n当前目标、需求解释与验收以 [spec/project.md](spec/project.md) 和 [requirements.json](spec/requirements.json) 为入口；[status.md](spec/status.md) 是派生快照，[tasks.md](spec/tasks.md) 是下一动作。旧正文、计划和实验保留为历史或证据，旧的“最新状态”不覆盖当前有效合同。')
    managed_entry(root,'AGENTS.md','## Spec管理\n\n开始项目工作时先读 spec/project.md、spec/requirements.json、spec/status.md，再读当前任务相关的 spec/tasks.md 和实现。每次实际进展更新对应验收证据与任务；解释不明要标明，不把自定指标当用户需求。实验结束与需求通过分别记录。用户新指令优先，保留已有权限及活跃作业约束；开启模式不自动授权训练、发布、收费或创建goal。')
    return refresh(root)

def field(data,key):
    for part in key.split('.'):
        if isinstance(data,list):data=data[int(part)]
        else:data=data[part]
    return data

def check(root):
    path=root/'spec/requirements.json';data=json.loads(path.read_text(encoding='utf-8'));errors=[];reqs=data.get('requirements',[])
    if data.get('schema_version')!=1:errors.append('Unsupported schema_version')
    if not isinstance(data.get('project'),dict) or not data['project'].get('objective'):errors.append('Missing project objective')
    if not isinstance(reqs,list):raise ValueError('requirements must be a list')
    ids=[r.get('id') for r in reqs]
    if len(ids)!=len(set(ids)):errors.append('Duplicate requirement IDs')
    if any(not isinstance(i,str) or not re.fullmatch(r'r\d{3,}',i) for i in ids):errors.append('IDs must look like r001')
    for r in reqs:
        rid=r.get('id','?');status=r.get('status');meaning=r.get('interpretation',{})
        if not r.get('title') or not r.get('requirement') or not r.get('source'):errors.append(f'{rid}: missing title/requirement/source')
        if not isinstance(r.get('required'),bool):errors.append(f'{rid}: required must be boolean')
        if status not in STATES:errors.append(f'{rid}: invalid status')
        if meaning.get('state') not in {'confirmed','proposed','ambiguous'} or not meaning.get('statement'):errors.append(f'{rid}: missing/invalid interpretation')
        criteria=r.get('acceptance',[])
        if not criteria:errors.append(f'{rid}: no acceptance criteria')
        cids=[a.get('id') for a in criteria]
        if len(cids)!=len(set(cids)):errors.append(f'{rid}: duplicate acceptance IDs')
        for a in criteria:
            label=f'{rid}/{a.get("id","?")}'
            if a.get('status') not in AC_STATES or not a.get('text'):errors.append(f'{label}: invalid criterion')
            evidence=a.get('evidence',[])
            if a.get('status')=='verified' and not evidence:errors.append(f'{label}: verified without evidence')
            for e in evidence:
                if not e.get('scope') or e.get('kind') not in {'audit','human_review','contract','diagnostic'}:errors.append(f'{label}: evidence requires kind and scope')
                q=Path(e.get('path',''));q=q if q.is_absolute() else root/q
                if not q.is_file():errors.append(f'{label}: missing evidence {q}');continue
                if e.get('sha256') and hashlib.sha256(q.read_bytes()).hexdigest()!=e['sha256']:errors.append(f'{label}: changed evidence SHA {q}')
                if e.get('check'):
                    try:
                        v=field(json.loads(q.read_text(encoding='utf-8')),e['check']['field'])
                        if json.dumps(v,sort_keys=True)!=json.dumps(e['check']['equals'],sort_keys=True):errors.append(f'{label}: JSON evidence condition failed {q}')
                    except (ValueError,KeyError,IndexError,TypeError):errors.append(f'{label}: invalid JSON evidence condition {q}')
        if status=='verified':
            if meaning.get('state')!='confirmed' or any(a.get('status')!='verified' for a in criteria):errors.append(f'{rid}: verified with unresolved meaning/criteria')
        for dep in r.get('depends_on',[]):
            if dep not in ids:errors.append(f'{rid}: unknown dependency {dep}')
            if dep==rid:errors.append(f'{rid}: self dependency')
            if status=='verified' and dep in ids and next(x for x in reqs if x['id']==dep)['status']!='verified':errors.append(f'{rid}: verified before dependency {dep}')
    graph={r.get('id'):r.get('depends_on',[]) for r in reqs};seen=set();active=set()
    def visit(n):
        if n in active:errors.append('Dependency cycle involving '+str(n));return
        if n in seen:return
        active.add(n)
        for dep in graph.get(n,[]):visit(dep)
        active.remove(n);seen.add(n)
    for n in graph:visit(n)
    task=root/'spec/tasks.md'
    if task.exists():
        unknown=set(re.findall(r'\br\d{3,}\b',task.read_text(encoding='utf-8')))-set(ids)
        if unknown:errors.append('Unknown requirement links in tasks: '+','.join(sorted(unknown)))
    return data,errors

def refresh(root):
    data,errors=check(root)
    if errors:raise ValueError('\n'.join(errors))
    reqs=data['requirements'];required=[r for r in reqs if r['required']];complete=bool(required) and data['project'].get('scope_review')=='confirmed' and all(r['status']=='verified' for r in required)
    rows=['# 当前Spec状态','',f"项目：{data['project'].get('name',root.name)}",'',f"本次必需需求：{len(required)}；已验收：{sum(r['status']=='verified' for r in required)}；记录上的完成条件：{'满足' if complete else '尚未满足'}。",'','这是 requirements.json 的派生快照。检查器只能核对结构/显式证据条件；最终仍须按完整需求范围确认，不由这个页面自动结束goal。','','|需求|优先级|状态|主要阻碍|','|---|---|---|---|']
    for r in sorted(reqs,key=lambda r:(r.get('priority',9),r['id'])):rows.append('|'+r['id']+' '+r['title'].replace('|','/')+'|'+str(r.get('priority',9))+'|'+r['status']+'|'+(r.get('blocker') or '').replace('|','/').replace('\n',' ')+'|')
    rows+=['','下一动作见 [tasks.md](tasks.md)；解释与版本见 [decisions.md](decisions.md)。']
    atomic_write(root/'spec/status.md','\n'.join(rows)+'\n')
    return {'structure_passed':True,'requirements':len(reqs),'required_verified':sum(r['status']=='verified' for r in required),'recorded_completion_criteria_met':complete,'semantic_completion_proven_by_tool':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['init','check','refresh']);p.add_argument('--root',type=Path,required=True);p.add_argument('--name');a=p.parse_args();root=a.root.resolve()
    if not root.is_dir():p.error('Root must be an existing explicitly selected project directory')
    try:
        if a.command=='init':result=init(root,a.name)
        elif a.command=='refresh':result=refresh(root)
        else:
            data,errors=check(root);result={'structure_passed':not errors,'errors':errors,'requirements':len(data['requirements']),'semantic_completion_proven_by_tool':False}
            if errors:print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(1)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,TypeError) as exc:p.exit(1,str(exc)+'\n')
if __name__=='__main__':main()
