import {useCallback,useEffect,useState} from 'react';
import {Database,Fingerprint,Link2,Unlink,RefreshCw,ShieldAlert,GitMerge,History} from 'lucide-react';
import {useResearchData} from '../hooks/useResearch';
import {getObservationState,getIdentityState,createIdentityLink,revokeIdentityLink,resumeIdentityVersion} from '../api/identity';
import {IdentityState,ObservationState} from '../types/domain';
import {useAuth} from '../stores/auth';
import {useAction} from '../hooks/useAction';
import {DataTable} from '../components/common/DataTable';
import {Notice} from '../components/common/Notice';
import {number,time} from '../utils/format';
import {IdentityVersionBadge} from '../components/IdentityVersionBadge';

export function Datasets(){
  const s=useResearchData(),{user}=useAuth(),action=useAction();
  const d=s.datasets[0];
  const [observationState,setObservationState]=useState<ObservationState|null>(null);
  const [identity,setIdentity]=useState<IdentityState|null>(null);
  const [error,setError]=useState('');
  const [a,setA]=useState(''),[b,setB]=useState('');
  const editable=user?.role==='admin'||user?.role==='analyst';
  const building=identity?.building_version;

  const load=useCallback(async()=>{
    if(!d)return;
    try{
      const [rows,state]=await Promise.all([getObservationState(d.id),getIdentityState(d.id)]);
      setObservationState(rows);setIdentity(state);setError('');
    }catch(e){setError((e as Error).message)}
  },[d?.id]);

  useEffect(()=>{load()},[load]);
  useEffect(()=>{
    if(!building)return;
    const id=setInterval(load,2000);
    return ()=>clearInterval(id);
  },[building?.id,load]);

  async function link(){
    if(!d)return;
    await action.execute(async()=>{
      await createIdentityLink(d.id,{subject_a:a.trim(),subject_b:b.trim()});
      setA('');setB('');
      await load();await s.refresh();
    });
  }
  async function revoke(linkId:number){
    if(!d)return;
    await action.execute(async()=>{await revokeIdentityLink(d.id,linkId);await load();await s.refresh()});
  }
  async function resume(versionId:number){
    if(!d)return;
    await action.execute(async()=>{await resumeIdentityVersion(d.id,versionId);await load()});
  }

  const published=identity?.published_version;
  const summary=observationState?.summary;

  return <div className="page"><div className="page-title"><div><p className="eyebrow">OBSERVATION REGISTER / 02</p><h1>观测数据<span>让每个样本有据可查。</span></h1></div><Database size={32}/></div>
    <Notice message={s.error||error||action.error} retry={()=>{s.refresh();load()}}/>
    {d?<>
      <div className="dataset-banner"><div><span className="eyebrow">FROZEN SOURCE / 合成可复现</span><h2>{d.name}</h2><p>{d.description}</p><code>{d.source}</code></div><div><strong>{d.observations.toLocaleString()}</strong><span>独立用户观测</span><small><Fingerprint size={15}/>{d.checksum.slice(0,16)}…</small></div></div>

      {summary&&<div className="identity-metrics">
        <div><span>匿名标识</span><b>{summary.subjects.toLocaleString()}</b></div>
        <div><span>合并后自然人</span><b>{summary.people.toLocaleString()}</b></div>
        <div><span>同组合并</span><b>{summary.merged_people} <small>/ {summary.merged_observations} 条观测</small></b></div>
        <div><span>跨组隔离</span><b>{summary.quarantined_people} <small>/ {summary.quarantined_observations} 条观测</small></b></div>
        <div><span>当前身份版本</span><b className="identity-version-cell"><IdentityVersionBadge version={published}/></b></div>
      </div>}

      <div className="section-heading"><h2>匿名身份关联</h2><span>IDENTITY LINKS / 可传递 · 可撤销</span></div>
      <div className="identity-layout">
        <section>
          {editable?<div className="identity-form">
            <label>匿名标识 A<input aria-label="匿名标识 A" value={a} onChange={e=>setA(e.target.value)} placeholder="例如 LQ-00001"/></label>
            <label>匿名标识 B<input aria-label="匿名标识 B" value={b} onChange={e=>setB(e.target.value)} placeholder="例如 LQ-00002"/></label>
            <button className="primary" disabled={action.busy||!a.trim()||!b.trim()||!!building} onClick={link}><Link2 size={15}/>{action.busy?'提交中…':'关联同一人'}</button>
            <small>{building?'身份版本构建中，关联修改会在发布后开放。':'关联可传递；同一人的同组收入合并后再分析。'}</small>
          </div>:<p className="role-note">观察员可阅读全部关联与版本；维护关联需要分析师或负责人权限。</p>}

          <DataTable head={['关联对','状态','登记版本','撤销版本','操作者','时间','操作']}>
            {(identity?.links??[]).slice().reverse().map(l=><tr key={l.id}>
              <td className="mono">{l.subject_a} ↔ {l.subject_b}</td>
              <td>{l.revoked_at?<span className="link-state revoked">已撤销</span>:<span className="link-state active">生效中</span>}</td>
              <td className="mono">{l.added_version_id?`v${identity?.versions.find(v=>v.id===l.added_version_id)?.number??'…'}`:'—'}</td>
              <td className="mono">{l.revoked_version_id?`v${identity?.versions.find(v=>v.id===l.revoked_version_id)?.number??'…'}`:'—'}</td>
              <td>{l.revoked_at?l.revoked_by:l.created_by}</td>
              <td>{time(l.revoked_at||l.created_at)}</td>
              <td>{editable&&!l.revoked_at?<button className="text-button" disabled={action.busy||!!building} onClick={()=>revoke(l.id)}><Unlink size={14}/>撤销</button>:null}</td>
            </tr>)}
          </DataTable>
          {!identity?.links.length&&<div className="empty">尚无关联。所有匿名标识当前各自独立计为一人。</div>}
        </section>

        <aside className="identity-side">
          <h2><History size={17}/>身份版本</h2>
          {building&&<div className="build-progress">
            <div><b>v{building.number} 构建中</b><span>{building.completed_batches} / {building.total_batches} 批</span></div>
            <progress max={Math.max(1,building.total_batches)} value={building.completed_batches}/>
            <p>发布前继续使用完整旧版；中断后可恢复，完成后统一切换。</p>
            <button className="outline" disabled={action.busy} onClick={()=>resume(building.id)}><RefreshCw size={14}/>继续构建</button>
          </div>}
          <ol className="version-list">{(identity?.versions??[]).map(v=><li key={v.id}>
            <div><b>v{v.number}</b><IdentityVersionBadge version={v}/></div>
            <p>{v.summary?.note||'身份版本'}</p>
            <small>{v.status==='published'?`发布于 ${time(v.published_at||v.created_at)}`:`${v.created_by} · ${time(v.created_at)}`}{typeof v.summary?.changed_subjects==='number'?` · 改写 ${v.summary.changed_subjects} 位`:''}</small>
          </li>)}</ol>
          {!identity?.versions.length&&<p className="tiny">登记第一个关联或实验时建立基线版本 v1。</p>}
        </aside>
      </div>

      {(identity?.groups??[]).length>0&&<>
        <div className="section-heading"><h2>已合并的自然人</h2><span>GROUPS / 传递合并结果</span></div>
        <div className="group-grid">{identity!.groups.map(g=><article key={g.person_key} className={g.cross_arm?'cross-arm':''}>
          <div><b>{g.person_key}</b>{g.cross_arm?<span className="cross-flag"><ShieldAlert size={13}/>跨组隔离</span>:<span className="merge-flag"><GitMerge size={13}/>合并</span>}</div>
          <p className="mono">{g.subjects.join(' · ')}</p>
          <small>{g.cross_arm?g.reason:`同属 ${g.arms.map(x=>x==='control'?'对照组':'候选组').join('/')}，收入相加后计为一人`}</small>
        </article>)}</div>
      </>}

      {summary&&summary.quarantined_people>0&&<>
        <div className="section-heading"><h2>跨组身份隔离</h2><span>QUARANTINE / 整人不参与分析</span></div>
        <DataTable head={['自然人','匿名标识','涉及分组','观测数','原因']}>
          {summary.quarantine.map(q=><tr key={q.person_key}><td className="mono">{q.person_key}</td><td className="mono">{q.subjects.join(' · ')}</td><td>{q.arms.map(x=>x==='control'?'对照':'候选').join(' / ')}</td><td>{q.observations??'—'}</td><td>{q.reason}</td></tr>)}
        </DataTable>
      </>}

      <div className="section-heading"><h2>样本预览</h2><span>前 100 条 / 附带自然人与版本</span></div>
      <DataTable head={['匿名用户','所属自然人','实验分组','前置7日收入 / 元','后置7日收入 / 元','身份标记']}>
        {(observationState?.rows??[]).map(o=><tr key={o.subject}>
          <td className="mono">{o.subject}</td>
          <td className="mono">{o.person_key}</td>
          <td><span className={'arm '+o.arm}>{o.arm==='control'?'对照组':'候选组'}</span></td>
          <td>{number(o.pre_value)}</td><td>{number(o.value)}</td>
          <td>{o.quarantined?<span className="link-state revoked">跨组隔离</span>:o.merged?<span className="link-state active">已合并</span>:<span className="tiny">独立</span>}</td>
        </tr>)}
      </DataTable>
      <p className="assumptions">预览与摘要均基于身份版本 {published?`v${published.number}`:'—'}；合并与隔离口径可在分析结果与导出文件中追溯。</p>
    </>:<p>尚无观测数据，请联系实验负责人执行 seed。</p>}
  </div>;
}
