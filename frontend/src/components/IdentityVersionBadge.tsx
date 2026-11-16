import {IdentityVersion} from '../types/domain';

const Labels:Record<string,string>={building:'构建中',published:'已发布',superseded:'已被取代',failed:'构建失败'};

export function IdentityVersionBadge({version}:{version?:IdentityVersion|null}){
  if(!version)return <span className="version-badge none">无版本</span>;
  return <span className={'version-badge '+version.status}>
    <i/>v{version.number} · {Labels[version.status]||version.status}
    {version.status==='building'?` ${version.completed_batches}/${version.total_batches}`:''}
  </span>;
}
