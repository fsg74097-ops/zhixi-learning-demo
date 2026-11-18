import { useCallback, useEffect, useRef, useState } from 'react';
import { api, viewPath } from './api.ts';
import type { JobState } from './api.ts';
import type { Scope, ViewModel } from './models.ts';
export function useView(view: string, scope: Scope, revision: number, recordId?: string) {
  const [data,setData]=useState<ViewModel>(); const [error,setError]=useState<unknown>(); const [loading,setLoading]=useState(true);
  const key=JSON.stringify(scope);
  useEffect(()=>{ const controller=new AbortController(); setLoading(true);setError(undefined);setData(undefined);
    api.get<ViewModel>(viewPath(view,{...scope,record_id:recordId}),controller.signal).then(value=>{if(!controller.signal.aborted)setData(value);}).catch(error=>{if(!controller.signal.aborted)setError(error);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[view,key,revision,recordId]);
  return {data,error,loading};
}
export function useCommand(identity: string) {
  const [result,setResult]=useState<unknown>(); const [error,setError]=useState<unknown>(); const [busy,setBusy]=useState(false); const [progress,setProgress]=useState<JobState>(); const ref=useRef<AbortController | undefined>(undefined);
  useEffect(()=>{setResult(undefined);setError(undefined);setProgress(undefined);setBusy(false);return()=>ref.current?.abort();},[identity]);
  const run=useCallback(async(path:string,body:unknown)=>{ref.current?.abort();const controller=new AbortController();ref.current=controller;setBusy(true);setError(undefined);setResult(undefined);setProgress(undefined);try{const value=await api.run(path,body,{signal:controller.signal,onProgress:setProgress});setResult(value);return value;}catch(error){if(error instanceof Error&&error.name!=='AbortError')setError(error);throw error;}finally{if(!controller.signal.aborted)setBusy(false);}},[]);
  return {result,error,busy,progress,run,cancel:()=>{ref.current?.abort();setBusy(false);}};
}
