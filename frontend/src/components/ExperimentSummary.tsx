import {Experiment} from '../types/domain';
import {IdentityVersionBadge} from './IdentityVersionBadge';

export function ExperimentSummary({experiment}:{experiment?:Experiment}){
  return <div className="experiment-summary"><span className="eyebrow">REGISTERED HYPOTHESIS / 预注册假设</span><h3>{experiment?.name||'请先选择实验'}</h3><p>{experiment?.hypothesis||'建立实验后即可提交独立的分析任务。'}</p>{experiment&&<small>负责人 {experiment.owner} · α {experiment.alpha} · {experiment.cuped?'CUPED 校正':'原始指标'} · 身份版本 <IdentityVersionBadge version={experiment.identity_version}/></small>}</div>;
}
