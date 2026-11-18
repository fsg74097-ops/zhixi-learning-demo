"""Bounded background commands; results are visible only to their demo session."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
from time import time
from uuid import uuid4


class JobStore:
    def __init__(self, max_workers=2, max_jobs=128):
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='zhixi-command')
        self.lock = Lock()
        self.jobs = {}
        self.max_jobs = max_jobs

    def submit(self, owner, operation):
        with self.lock:
            if sum(item['status'] in {'QUEUED','RUNNING'} for item in self.jobs.values()) >= 8:
                raise ValueError('JOB_CAPACITY_EXCEEDED')
            if len(self.jobs) >= self.max_jobs:
                completed = [key for key,value in self.jobs.items() if value['status'] in {'SUCCEEDED','FAILED'}]
                if not completed:
                    raise ValueError('JOB_CAPACITY_EXCEEDED')
                self.jobs.pop(completed[0])
            identifier = 'JOB-' + uuid4().hex
            self.jobs[identifier] = {'job_id':identifier,'owner':owner,'status':'QUEUED',
                'progress':['任务已排队'],'result':None,'error':None,'created_at':time()}

        def run():
            with self.lock:
                self.jobs[identifier].update(status='RUNNING', progress=['正在执行受控工具与证据核验'])
            try:
                result = operation()
                status = 'SUCCEEDED' if result.get('ok') else 'FAILED'
                with self.lock:
                    self.jobs[identifier].update(status=status, result=result,
                        error=result.get('error'),progress=['任务已完成，可查看证据与限制'])
            except Exception:
                with self.lock:
                    self.jobs[identifier].update(status='FAILED',error={'code':'COMMAND_FAILED',
                        'message':'任务未完成，已有证据保持不变。','retryable':False},
                        progress=['任务执行失败，请核对输入或稍后重试'])

        self.pool.submit(run)
        return self.get(identifier, owner)

    def get(self, identifier, owner):
        with self.lock:
            item = self.jobs.get(identifier)
            if item is None or item['owner'] != owner:
                raise ValueError('JOB_NOT_FOUND')
            return deepcopy({key:value for key,value in item.items() if key != 'owner'})

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
