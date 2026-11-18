import type { Context, Scope, ViewModel } from '../models.ts';
export interface FeatureProps {data:ViewModel;context:Context;scope:Scope;entity?:string;onEvidence:(id:string)=>void;onNavigate:(target:string)=>void;onRefresh:()=>void;notify:(message:string)=>void}
