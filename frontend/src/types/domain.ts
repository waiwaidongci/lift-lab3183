export interface User {name:string;email:string;role:string}
export interface Dataset {id:number;name:string;source:string;description:string;checksum:string;observations:number}
export interface Observation {subject:string;arm:string;pre_value:number;value:number}
export interface Experiment {id:number;name:string;dataset_id:number;hypothesis:string;alpha:number;cuped:boolean;owner:string;identity_version?:IdentityVersion|null}
export interface Comparison {control_mean:number;treatment_mean:number;difference:number;p_value:number;ci_low:number;ci_high:number;n_control:number;n_treatment:number;standard_error:number}
export interface Run {id:number;experiment_id:number;status:string;error:string;created_at:string;result:{raw?:Comparison;cuped?:Comparison;theta?:number;variance_reduction?:number;significant?:boolean;interpretation?:string;assumptions?:string;identity?:IdentityProvenance}}
export interface Decision {id:number;run_id:number;outcome:string;note:string;author:string;created_at:string}
export interface Audit {id:number;actor:string;action:string;target:string;created_at:string}

export interface IdentityVersion {id:number;dataset_id:number;number:number;status:string;base_version_id:number|null;created_by:string;created_at:string;published_at:string|null;superseded_at:string|null;total_batches:number;completed_batches:number;batch_size:number;pending_batches:number;is_current:boolean;summary:{note?:string;affected_subjects?:number;changed_subjects?:number;people?:number;subjects?:number};error:string}
export interface IdentityLink {id:number;dataset_id:number;subject_a:string;subject_b:string;created_by:string;created_at:string;revoked_by:string|null;revoked_at:string|null;added_version_id:number|null;revoked_version_id:number|null}
export interface IdentityGroup {person_key:string;subjects:string[];arms:string[];cross_arm:boolean;reason:string|null}
export interface IdentityQuarantine {person_key:string;subjects:string[];arms:string[];observations:number;reason:string}
export interface IdentityState {dataset_id:number;published_version:IdentityVersion|null;building_version:IdentityVersion|null;links:IdentityLink[];active_link_count:number;groups:IdentityGroup[];versions:IdentityVersion[]}
export interface IdentitySummary {observations:number;subjects:number;people:number;merged_people:number;merged_observations:number;quarantined_people:number;quarantined_observations:number;quarantine:IdentityQuarantine[]}
export interface ObservationState {dataset_id:number;identity_version:number|null;identity_version_id:number|null;rows:ObservationRow[];summary:IdentitySummary;quarantine:IdentityQuarantine[]}
export interface ObservationRow extends Observation {person_key:string;identity_version:number|null;identity_version_id:number|null;merged:boolean;quarantined:boolean}
export interface IdentityProvenance {observations:number;subjects:number;people:number;merged_people:number;merged_observations:number;quarantined_people:number;quarantined_observations:number;quarantine:IdentityQuarantine[];version_summary:IdentityVersion;pinned:boolean}
