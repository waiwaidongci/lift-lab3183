import {request} from './client';
import {IdentityState,ObservationState} from '../types/domain';

export const getObservationState=(id:number)=>request<ObservationState>(`/datasets/${id}/observation-state`);
export const getIdentityState=(id:number)=>request<IdentityState>(`/datasets/${id}/identity`);
export const createIdentityLink=(id:number,data:{subject_a:string;subject_b:string;batch_size?:number})=>request<{link:unknown;version:unknown}>(`/datasets/${id}/identity/links`,{method:'POST',body:JSON.stringify(data)});
export const revokeIdentityLink=(id:number,linkId:number,batchSize=200)=>request<{link:unknown;version:unknown}>(`/datasets/${id}/identity/links/${linkId}/revoke`,{method:'POST',body:JSON.stringify({batch_size:batchSize})});
export const resumeIdentityVersion=(id:number,versionId:number)=>request(`/datasets/${id}/identity/versions/${versionId}/resume`,{method:'POST'});
