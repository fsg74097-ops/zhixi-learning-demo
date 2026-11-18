"""Session-bound presentation events. Business decisions are handled separately."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


CLIENT_FIELDS = {
    'NODE_SELECTED': ({'node_id','selected'}, set()),
    'EDGE_SELECTED': ({'source','target'}, {'relation_type'}),
    'EVIDENCE_OPENED': ({'entity_id'}, set()),
    'FILTER_CHANGED': (set(), {'class_id','student_id','filter_id'}),
    'TIME_RANGE_CHANGED': ({'node_id'}, {'comparison_node_id'}),
    'VIEW_CHANGED': ({'view_id'}, set()),
}


class UIEventInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.1']
    event_type: str = Field(min_length=1,max_length=64)
    trace_id: str = Field(min_length=1,max_length=80)
    snapshot_hash: str = Field(min_length=1,max_length=128)
    payload: dict[str, StrictStr | StrictBool | StrictInt] = Field(max_length=4)
    client_event_id: str = Field(min_length=1,max_length=120)

    def has_valid_payload(self):
        required, optional = CLIENT_FIELDS[self.event_type]
        keys = self.payload.keys()
        if not keys or not required <= keys <= required | optional:
            return False
        return all(isinstance(value,bool) if key == 'selected'
                   else isinstance(value,str) and len(value) <= 180
                   and (bool(value) or key == 'class_id')
                   for key,value in self.payload.items())


class UIEventJournal:
    def __init__(self, directory):
        self.path = Path(directory) / 'ui_events.sqlite3'

    def _connection(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            db.execute(
                'CREATE TABLE IF NOT EXISTS ui_events ('
                'sequence INTEGER PRIMARY KEY, session_trace TEXT NOT NULL, client_event_id TEXT NOT NULL, '
                'event TEXT NOT NULL, UNIQUE(session_trace,client_event_id))')
        except sqlite3.Error:
            db.close()
            raise
        return db

    def append(self, session_trace, event):
        encoded = json.dumps(event,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        identity = (session_trace,event['client_event_id'])
        with closing(self._connection()) as db, db:
            cursor = db.execute(
                'INSERT INTO ui_events(session_trace,client_event_id,event) VALUES(?,?,?) '
                'ON CONFLICT(session_trace,client_event_id) DO NOTHING', (*identity,encoded))
            if cursor.rowcount:
                return True
            original, = db.execute(
                'SELECT event FROM ui_events WHERE session_trace=? AND client_event_id=?', identity).fetchone()
            return original == encoded

    def recent(self, session_trace, limit=100):
        with closing(self._connection()) as db:
            rows = db.execute(
                'SELECT event FROM ui_events WHERE session_trace=? ORDER BY sequence DESC LIMIT ?',
                (session_trace,limit)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]
