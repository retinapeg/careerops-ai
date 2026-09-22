"""Persistent, explicitly scoped local draft batches. Never submits applications."""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone

from careerops.store import digest, encode, now


class Preparation:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.active = set()
        self.cancelled = {}

    def preview(self, data):
        from careerops.inventory import query_inventory, classify_job
        from careerops.materials import PROMPT_VERSION
        jobs = self.store.jobs()
        if 'job_ids' in data:
            raw = data['job_ids']
            if not isinstance(raw, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in raw):
                raise ValueError('Select explicit job IDs.')
            ids = list(dict.fromkeys(raw))
        else:
            count = data.get('count', 10)
            if count not in {10, 25, 50}:
                raise ValueError('Choose the next 10, 25 or 50 from a filtered view.')
            filters = data.get('filters')
            if not isinstance(filters, dict) or not filters.get('region'):
                raise ValueError('Select a filtered inventory view first.')
            ids = query_inventory(jobs, self.store.settings(), filters)['all_matching_ids'][:count]
        if not ids or len(ids) > 500:
            raise ValueError('Select between 1 and 500 jobs.')
        by_id = {j['id']: j for j in jobs}
        profile = self.store.profile()
        selected, warnings = [], []
        for job_id in ids:
            if job_id not in by_id:
                raise ValueError('One selected opportunity no longer exists. Refresh the view.')
            job = by_id[job_id]
            info = classify_job(job, self.store.settings())
            if job['status'] in {'applied', 'interview', 'offer', 'closed', 'dismissed'} or info['eligibility'] == 'blocked' or info['verification'] == 'closed':
                raise ValueError(f"'{job['title']}' is blocked, closed or already acted on. Remove it from this preparation scope.")
            selected.append({'job_id': job_id, 'id': job_id, 'title': job['title'], 'company': job['company'],
                             'url': job.get('url'), 'status': 'pending', 'eligibility': info['eligibility'],
                             'verification': info['verification'], 'role_family': info['role_family'],
                             'content_fingerprint': job['content_fingerprint'], 'warnings': job['evaluation'].get('gaps', [])})
        if any(j['verification'] != 'verified_open' for j in selected):
            warnings.append('Some adverts still need live verification; preparing does not confirm that they remain open.')
        warnings.append('Creates drafts only. Eligibility questions remain unanswered until reviewed; no submissions or messages.')
        value = {'preview_id': secrets.token_urlsafe(20), 'created_at': now(), 'jobs': selected, 'count': len(selected),
                 'profile_hash': digest(profile), 'generation_version': PROMPT_VERSION, 'filters': data.get('filters'),
                 'billing_mode': 'free_local', 'budget_usd': 0, 'warnings': warnings}
        with self.store.lock, self.store.connect() as db:
            db.execute('INSERT INTO preparation_previews VALUES (?,?)', (value['preview_id'], encode(value)))
        return value

    def start(self, preview_id):
        from careerops.materials import PROMPT_VERSION
        with self.lock, self.store.lock, self.store.connect() as db:
            if self.active:
                raise ValueError('A preparation batch is already running.')
            row = db.execute('SELECT data FROM preparation_previews WHERE id=?', (preview_id,)).fetchone()
            if not row:
                raise ValueError('Preview this exact selection before starting.')
            preview = json.loads(row[0])
            if preview.get('batch_id'):
                return self.get(preview['batch_id'])
            if (datetime.now(timezone.utc) - datetime.fromisoformat(preview['created_at'])).total_seconds() > 3600:
                raise ValueError('This preview expired. Preview the current selection again.')
            if preview['profile_hash'] != digest(self.store.profile()) or preview['generation_version'] != PROMPT_VERSION:
                raise ValueError('Candidate evidence or generation policy changed. Preview again.')
            for item in preview['jobs']:
                current = self.store.get_job(item['job_id'])
                if current['content_fingerprint'] != item['content_fingerprint'] or current['status'] in {'applied','interview','offer','closed','dismissed'}:
                    raise ValueError('A selected job changed since preview. Preview the exact scope again.')
            batch = {'created_at': now(), 'status': 'running', 'total': preview['count'], 'completed': 0, 'failed': 0,
                     'items': preview['jobs'], 'profile_hash': preview['profile_hash'], 'generation_version': PROMPT_VERSION,
                     'billing_mode': 'free_local', 'budget_usd': 0, 'preview_id': preview_id, 'filters': preview.get('filters')}
            batch['id'] = db.execute('INSERT INTO preparation_batches(data) VALUES (?)', (encode(batch),)).lastrowid
            preview['batch_id'] = batch['id']
            db.execute('UPDATE preparation_previews SET data=? WHERE id=?', (encode(preview), preview_id))
            self.active.add(batch['id'])
        self._launch(batch)
        return batch

    def get(self, batch_id):
        with self.store.connect() as db:
            row = db.execute('SELECT data FROM preparation_batches WHERE id=?', (batch_id,)).fetchone()
            if not row:
                raise KeyError('Preparation batch not found')
            return dict(json.loads(row[0]), id=batch_id)

    def _launch(self, batch):
        with self.lock:
            self.active.add(batch['id'])
            event = threading.Event()
            self.cancelled[batch['id']] = event
        threading.Thread(target=self._worker, args=(batch, event), daemon=True, name=f"careerops-prepare-{batch['id']}").start()

    def cancel(self, batch_id):
        with self.lock:
            if batch_id not in self.active:
                raise ValueError('This batch has no active worker.')
            self.cancelled[batch_id].set()
            batch = self.get(batch_id)
            batch['status'] = 'cancelling'
            self.store.update_preparation_batch(batch_id, batch)
            return batch

    def resume(self, batch_id):
        from careerops.materials import PROMPT_VERSION
        with self.lock:
            if self.active:
                raise ValueError('Wait for the active preparation batch to stop.')
            batch = self.get(batch_id)
            if batch['status'] not in {'interrupted', 'cancelled', 'completed_with_errors'}:
                raise ValueError('Only a stopped batch can resume.')
            if batch['profile_hash'] != digest(self.store.profile()) or batch['generation_version'] != PROMPT_VERSION:
                raise ValueError('Inputs changed; preview a new batch to regenerate current materials.')
            for item in batch['items']:
                if item['status'] in {'running', 'failed'}:
                    item['status'] = 'pending'
                    item.pop('error', None)
            batch['status'] = 'running'
            batch['failed'] = 0
            self.store.update_preparation_batch(batch_id, batch)
            self.active.add(batch_id)
        self._launch(batch)
        return batch

    def _worker(self, batch, cancellation):
        from careerops.materials import prepare, export_material, validate_draft
        try:
            for item in batch['items']:
                if cancellation.is_set():
                    break
                if item['status'] == 'completed':
                    continue
                item['status'] = 'running'
                self.store.update_preparation_batch(batch['id'], batch)
                try:
                    with self.store.lock:
                        job = self.store.get_job(item['job_id'])
                        if digest(self.store.profile()) != batch['profile_hash'] or job['content_fingerprint'] != item['content_fingerprint']:
                            raise ValueError('Source inputs changed after scope confirmation; preview a fresh batch.')
                        if job['status'] in {'applied', 'interview', 'offer', 'closed', 'dismissed'} or job['evaluation'].get('eligibility') == 'blocked':
                            raise ValueError('Job was closed, blocked or acted on after scope confirmation.')
                        material = prepare(self.store, item['job_id'], family=item['role_family'], refresh=False)
                        validate_draft(material, self.store.profile())
                        exported = export_material(self.store, material['id'], 'docx')
                        from docx import Document
                        if not Document(exported).paragraphs:
                            raise ValueError('Generated document failed validation.')
                        item.update(status='completed', material_id=material['id'], document_validated=True)
                except Exception as exc:
                    item.update(status='failed', error=str(exc)[:300] if isinstance(exc, ValueError) else 'Draft failed validation or export. Existing materials are preserved; this job can be retried.')
                batch['completed'] = sum(i['status'] == 'completed' for i in batch['items'])
                batch['failed'] = sum(i['status'] == 'failed' for i in batch['items'])
                self.store.update_preparation_batch(batch['id'], batch)
            batch['status'] = 'cancelled' if cancellation.is_set() else 'completed_with_errors' if batch['failed'] else 'completed'
        except Exception:
            batch['status'] = 'interrupted'
        finally:
            self.store.refresh_shortlist(replace=False)
            with self.lock:
                if cancellation.is_set() and batch['completed'] < batch['total']:
                    batch['status'] = 'cancelled'
                batch['finished_at'] = now()
                self.store.update_preparation_batch(batch['id'], batch)
                self.active.discard(batch['id'])
