#!/usr/bin/env python3
"""Local, explicitly launched blind technical annotation; never loads predictions.

Only declared evaluation members are decoded. Labels never become subject
confirmations. The journal is private data and must not be committed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import offline_evaluation as oe
import evaluation_inputs as inputs
from ai_cull_assistant.ai_project import atomic_json
from ai_cull_assistant.publication_transaction import operation

SCHEMA = 'blind-annotation-session-v1'
ROLES = ('left_eye', 'right_eye', 'face_core', 'required_body', 'landscape_subject', 'texture')
VISIBILITY = ('visible', 'not_visible', 'insufficient_pixels', 'unjudgeable')
TECHNICAL = ('clear', 'unusable', 'borderline', 'unjudgeable')
ADEQUACY = ('adequate', 'insufficient', 'unknown')


def require(condition, message):
    if not condition:
        raise oe.ContractError(message)


def file_hash(path):
    return inputs.hash_file(Path(path))[0]


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _now():
    return datetime.now(timezone.utc).isoformat()


def safe_annotation_output(path, paths, *, resume=False):
    path = oe.outside_repository(Path(path))
    markers = ('scan-session.json', 'processing-settings.json', 'ai_project.json',
               'workspace-identity.json', '.publication-operation.lock')
    for ancestor in path.parents:
        require(not any((ancestor / marker).exists() for marker in markers),
                'annotation output may not be inside an active/existing workspace')
    if not resume and path.is_dir():
        require(not any((path / marker).exists() for marker in markers), 'output already exists; active workspace is not annotation output')
    for binding in paths['bindings'].values():
        for value in binding['sources'].values():
            source = Path(value).resolve()
            require(not path.is_relative_to(source.parent), 'annotation output may not be inside a listed input directory')
            require(not source.is_relative_to(path), 'annotation output overlaps an input source')
    return path


class AnnotationSession:
    """One atomic journal, optimistic revision checking, explicit new/resume."""
    def __init__(self, samples, paths, directory, *, resume=False):
        inputs.validate_samples(samples)
        self.samples = _copy(samples)
        self.rows = oe.unique_rows(samples['rows'], 'samples')
        require(paths.get('schema_version') == 'real-sample-path-map-v1', 'path-map version mismatch')
        self.manifest_hash = oe.canonical_hash(samples)
        require(paths.get('manifest_sha256') == self.manifest_hash, 'path-map manifest mismatch')
        self.paths = _copy(paths)
        inputs.validate_path_map(samples, paths)
        require(isinstance(paths.get('bindings'), dict) and set(paths['bindings']) == set(self.rows),
                'path-map must bind exactly the manifest assets')
        require(bool(samples.get('guideline_version')), 'explicit guideline version required')
        for aid, row in self.rows.items():
            require(isinstance(row.get('source_authorization'), str) and row['source_authorization'].strip(),
                    f'{aid}: source authorization required')
            sources = row.get('sources', [])
            require(isinstance(sources, list) and sources, f'{aid}: sources required')
            sids = [s.get('source_id') for s in sources]
            require(len(sids) == len(set(sids)), f'{aid}: duplicate source ID')
            require(row.get('evaluation_source_id') in sids, f'{aid}: explicit evaluation source required')
            mapped = paths['bindings'][aid].get('sources')
            require(isinstance(mapped, dict) and set(mapped) == set(sids), f'{aid}: member binding mismatch')
            for source in sources:
                problems = inputs.validate_source_registration(source, samples)
                require(not problems, f'{aid}: source registration incomplete: {problems}')
                oe.hash_field(source, 'sha256', aid)
                require(isinstance(mapped[source['source_id']], str) and Path(mapped[source['source_id']]).is_absolute(),
                        f'{aid}: absolute explicit member paths required')
        self.directory = safe_annotation_output(directory, paths, resume=resume)
        for row in self.rows.values():
            for value in paths['bindings'][row['asset_id']]['sources'].values():
                require(not Path(value).resolve().is_relative_to(self.directory), 'output overlaps source path')
        self.journal = self.directory / 'annotation-session.json'
        self.viewed = {}
        self.cost = {'hash_reads': 0, 'hash_seconds': 0.0, 'hash_bytes': 0, 'decode_seconds': 0.0}
        if resume:
            require(self.directory.is_dir() and self.journal.is_file(), 'not an annotation session')
            self.state = self._read()
        else:
            require(not self.directory.exists(), 'annotation output already exists; use explicit --resume only for own session')
            self.directory.mkdir(parents=True, exist_ok=False)
            self.state = {'schema_version': SCHEMA, 'session_id': str(uuid.uuid4()),
                          'manifest_sha256': self.manifest_hash,
                          'path_map_sha256': oe.canonical_hash(paths),
                          'guideline_version': samples['guideline_version'], 'revision': 0,
                          'created_at': _now(), 'ratings': [], 'adjudications': []}
            with operation(self.directory):
                atomic_json(self.journal, self.state)

    def _read(self):
        require(not self.journal.is_symlink(), 'annotation journal cannot be a symbolic link')
        state = oe.read_json(self.journal)
        require(state.get('schema_version') == SCHEMA, 'annotation session version mismatch')
        require(state.get('manifest_sha256') == self.manifest_hash, 'annotation manifest mismatch')
        require(state.get('path_map_sha256') == oe.canonical_hash(self.paths), 'annotation path-map mismatch')
        require(state.get('guideline_version') == self.samples['guideline_version'], 'annotation guideline mismatch')
        return state

    def source(self, asset_id):
        row = self.rows[asset_id]
        sid = row['evaluation_source_id']
        source = next(s for s in row['sources'] if s['source_id'] == sid)
        path = Path(self.paths['bindings'][asset_id]['sources'][sid])
        require(path.is_file(), f'{asset_id}: source missing')
        require(self._hash(path) == source['sha256'], f'{asset_id}: source content changed')
        return path, source

    def _hash(self, path):
        tick = time.perf_counter()
        try:
            digest, size = inputs.hash_file(Path(path))
            self.cost['hash_reads'] += 1
            self.cost['hash_bytes'] += size
            return digest
        finally:
            self.cost['hash_seconds'] += time.perf_counter() - tick

    def open_image(self, asset_id):
        # No scanner, filename pairing, model, prediction, or quality-score access.
        from ai_cull_assistant.image_decode import load_standard_image, load_raw_image
        from ai_cull_assistant.models import RAW_EXTENSIONS
        self.viewed.pop(asset_id, None)
        path, source = self.source(asset_id)
        tick = time.perf_counter()
        image = (load_raw_image(path, full_raw=True) if path.suffix.lower() in RAW_EXTENSIONS
                 else load_standard_image(path))
        self.cost['decode_seconds'] += time.perf_counter() - tick
        try:
            require(self._hash(path) == source['sha256'], f'{asset_id}: source changed during display decode')
            evidence = _copy(image.info.get('decode_evidence', {}))
            require(Path(evidence.get('source_path', '')).resolve() == path.resolve(), 'display source mismatch')
            require(evidence.get('full_resolution') is True, 'native annotation requires full-resolution decode; preview alone is insufficient')
            self.viewed[asset_id] = {'source_id': source['source_id'], 'sha256': source['sha256'],
                'decoded_dimensions': list(image.size), 'decode': evidence,
                'orientation': 'decoder-normalized; EXIF/raw orientation applied',
                'coordinate_space': 'oriented_native_decoded_pixels', 'viewed_at': _now()}
            return image, _copy(self.viewed[asset_id])
        except BaseException:
            image.close()
            raise

    def latest(self, asset_id, annotator_id):
        ratings = [r for r in self.state['ratings'] if r['asset_id'] == asset_id and r['annotator_id'] == annotator_id and r['status'] != 'withdrawn']
        return deepcopy(ratings[-1]) if ratings else None

    def latest_draft(self, asset_id, annotator_id):
        ratings = [r for r in self.state['ratings'] if r['asset_id'] == asset_id
                   and r['annotator_id'] == annotator_id and r['status'] == 'draft']
        return deepcopy(ratings[-1]) if ratings else None

    def effective_submission(self, asset_id, annotator_id):
        return next((deepcopy(r) for r in self._submitted(asset_id)
                     if r['annotator_id'] == annotator_id), None)

    def withdraw(self, asset_id, annotator_id, reason):
        require(isinstance(annotator_id, str) and annotator_id.strip(), 'annotator ID required')
        annotator_id = annotator_id.strip()
        require(isinstance(reason, str) and reason.strip(), 'withdrawal reason required')
        require(self.effective_submission(asset_id, annotator_id) is not None, 'no effective submission to withdraw')
        state = deepcopy(self.state)
        state['revision'] += 1
        state['ratings'].append({'asset_id': asset_id, 'annotator_id': annotator_id,
                                 'status': 'withdrawn', 'withdrawal_reason': reason,
                                 'revision': state['revision'], 'timestamp': _now()})
        self._write(state)

    def _validate_boxes(self, asset_id, opinion):
        width, height = self.viewed[asset_id]['decoded_dimensions']
        for region in opinion.get('regions', []):
            box = region.get('box')
            require(box is None or box[2] <= width and box[3] <= height, 'region outside native decoded dimensions')

    def _write(self, state):
        with operation(self.directory):
            require(not self.journal.with_suffix('.json.tmp').is_symlink(), 'annotation temporary path is a symbolic link')
            disk = self._read()
            require(disk['revision'] == self.state['revision'], 'annotation changed in another process; reopen before saving')
            atomic_json(self.journal, state)
        self.state = state

    def save(self, asset_id, annotator_id, opinion, *, submit=False):
        require(isinstance(annotator_id, str) and annotator_id.strip(), 'annotator ID required')
        require(asset_id in self.viewed, 'open the declared source before recording an opinion')
        self.source(asset_id)  # Do not attach an old opinion to changed pixels.
        validate_opinion(opinion, complete=submit)
        self._validate_boxes(asset_id, opinion)
        state = deepcopy(self.state)
        state['revision'] += 1
        rating = _copy(opinion)
        rating.update(asset_id=asset_id, annotator_id=annotator_id.strip(),
                      guideline_version=self.samples['guideline_version'], label_source='human',
                      status='submitted' if submit else 'draft', timestamp=_now(),
                      revision=state['revision'], cost_snapshot=_copy(self.cost), display_evidence=_copy(self.viewed[asset_id]))
        state['ratings'].append(rating)
        self._write(state)
        return rating

    def adjudicate(self, asset_id, annotator_id, opinion, reason):
        require(isinstance(annotator_id, str) and annotator_id.strip(), 'adjudicator ID required')
        require(isinstance(reason, str) and reason.strip(), 'adjudication reason required')
        require(asset_id in self.viewed, 'open source before adjudication')
        self.source(asset_id)
        validate_opinion(opinion, complete=True)
        self._validate_boxes(asset_id, opinion)
        latest = self._submitted(asset_id)
        require(len(latest) >= 2, 'adjudication requires at least two independently identified submitted raters')
        state = deepcopy(self.state)
        state['revision'] += 1
        record = _copy(opinion)
        record.update(asset_id=asset_id, annotator_id=annotator_id.strip(), adjudication_reason=reason,
                      rating_revisions=sorted(r['revision'] for r in latest), revision=state['revision'], timestamp=_now())
        state['adjudications'].append(record)
        self._write(state)

    def _submitted(self, asset_id):
        latest = {}
        for rating in self.state['ratings']:
            if rating['asset_id'] == asset_id and rating['status'] in ('submitted', 'withdrawn'):
                latest[rating['annotator_id']] = rating
        return [r for r in latest.values() if r['status'] == 'submitted']

    def export(self, output):
        # Explicit submission boundary: only this step creates evaluator input.
        require(self._read()['revision'] == self.state['revision'], 'session changed; reopen before export')
        document = {k: self.samples[k] for k in ('dataset_id', 'dataset_version', 'guideline_version', 'manifest_version')}
        document.update(schema_version=oe.SCHEMA, kind='annotations',
                        annotation_version=f"{self.state['session_id']}:{self.state['revision']}",
                        manifest_sha256=self.manifest_hash, rows=[], expert_consensus=False)
        for aid, sample in self.rows.items():
            ratings = self._submitted(aid)
            if not ratings:
                continue
            _, source = self.source(aid)
            revisions = sorted(r['revision'] for r in ratings)
            fields = ('technical_label', 'culling_label', 'final_selection', 'evidence_adequacy',
                      'regions', 'aesthetic_preference')
            def comparable(rating, field):
                value = rating.get(field)
                if field == 'regions':
                    # Wording is attributed evidence, not a technical vote.
                    value = [{k: v for k, v in region.items() if k != 'reason'}
                             for region in value or []]
                return value
            conflicts = [field for field in fields if len({oe.canonical_hash(comparable(r, field))
                                                          for r in ratings}) > 1]
            last_effective_change = max(r['revision'] for r in self.state['ratings']
                                        if r['asset_id'] == aid and r['status'] in ('submitted', 'withdrawn'))
            adjudications = [a for a in self.state['adjudications'] if a['asset_id'] == aid
                             and a['rating_revisions'] == revisions and a['revision'] > last_effective_change]
            adjudication = adjudications[-1] if adjudications else None
            unresolved = bool(conflicts) and adjudication is None
            legacy_adjudication = adjudication is not None and 'adjudication_reason' not in adjudication
            row = {}
            for field in (*fields, 'reason', 'exclusion_reason'):
                values = [r.get(field) for r in ratings]
                common = all(value == values[0] for value in values)
                row[field] = deepcopy(adjudication.get(field) if adjudication else values[0] if common else None)
            if adjudication is None:
                if 'regions' not in conflicts:
                    row['regions'] = deepcopy(ratings[0]['regions'])
                    for index, region in enumerate(row['regions']):
                        reasons = [r['regions'][index].get('reason') for r in ratings]
                        region['reason'] = reasons[0] if all(x == reasons[0] for x in reasons) else None
                else:
                    row['regions'] = []
                if 'evidence_adequacy' in conflicts:
                    row['evidence_adequacy'] = 'unknown'
                if unresolved:
                    row['reason'] = None
                # Preserve the evaluator's X reason contract without inventing consensus.
                if row['culling_label'] == 'X' and row['exclusion_reason'] is None:
                    row['exclusion_reason'] = json.dumps([
                        {'annotator_id': r['annotator_id'], 'reason': r.get('exclusion_reason')}
                        for r in ratings], ensure_ascii=False)
            if legacy_adjudication:
                # Older journals overwrote technical reason; do not fabricate recovery.
                row['reason'] = None
            row.update(asset_id=aid, sample_version=sample['sample_version'], evaluated_source_id=source['source_id'],
                       input_sha256=source['sha256'], label_source='human',
                       annotator_id=adjudication['annotator_id'] if adjudication else
                           ratings[0]['annotator_id'] if len(ratings) == 1 else f"aggregate:{self.state['session_id']}",
                       original_ratings=deepcopy(ratings), disagreement=bool(conflicts),
                       disagreement_fields=conflicts, unresolved_disagreement=unresolved,
                       unresolved_fields=conflicts if unresolved else [],
                       summary_basis='adjudication' if adjudication else 'fieldwise_agreement',
                       exclusion_reason_basis='per_rater_attribution' if adjudication is None and
                           row['culling_label'] == 'X' and not all(r.get('exclusion_reason') == ratings[0].get('exclusion_reason') for r in ratings)
                           else 'adjudication' if adjudication else 'agreement',
                       adjudication_reason=adjudication.get('reason') if legacy_adjudication else
                           adjudication.get('adjudication_reason') if adjudication else None,
                       legacy_technical_reason_unavailable=legacy_adjudication,
                       independent_rater_count=len(ratings), expert_consensus=False)
            document['rows'].append(row)
        output = safe_annotation_output(output, self.paths)
        require(not output.exists(), 'annotation export exists; choose new file')
        require(not output.resolve().is_relative_to(self.directory), 'export must be separate from annotation session')
        oe.write_new(output, document)
        return document


def validate_opinion(opinion, *, complete):
    require(isinstance(opinion, dict), 'opinion must be an object')
    for key, allowed in (('technical_label', TECHNICAL), ('culling_label', ('K', 'D', 'U', 'X')),
                         ('evidence_adequacy', ADEQUACY), ('final_selection', ('keep', 'reject', 'undecided'))):
        require(opinion.get(key) in (*allowed, None, ''), f'invalid {key}')
        if complete and key != 'final_selection':
            require(opinion.get(key) in allowed, f'explicit {key} required before submit')
    require(isinstance(opinion.get('regions', []), list), 'regions array required')
    for field in ('reason', 'exclusion_reason', 'aesthetic_preference'):
        require(isinstance(opinion.get(field, ''), str), f'{field} must be text')
    if complete:
        require(bool(opinion.get('reason', '').strip()), 'reason required')
        require(bool(opinion.get('regions')), 'at least one explicitly inspected region required')
    if opinion.get('culling_label') == 'X' and complete:
        require(bool(opinion.get('exclusion_reason', '').strip()), 'X requires exclusion reason')
    seen = set()
    for region in opinion.get('regions', []):
        require(isinstance(region, dict), 'region must be object')
        require(region.get('role') in (*ROLES, None, '') if not complete else region.get('role') in ROLES, 'invalid region role')
        require(isinstance(region.get('reason', ''), str), 'region reason must be text')
        if complete:
            require(bool(region.get('reason', '').strip()), 'region reason required')
        rid = oe.text_field(region, 'region_id', 'region')
        require(rid not in seen, 'duplicate region ID'); seen.add(rid)
        require(type(region.get('required')) is bool or not complete and region.get('required') is None, 'region required must be explicitly true/false')
        require(region.get('visibility') in (VISIBILITY if complete else (*VISIBILITY, None, '')), 'invalid region visibility')
        require(region.get('technical_label') in (TECHNICAL if complete else (*TECHNICAL, None, '')), 'invalid regional technical label')
        require(region.get('evidence_adequacy') in (ADEQUACY if complete else (*ADEQUACY, None, '')), 'invalid regional evidence adequacy')
        require(region.get('coordinate_space') == 'oriented_native_decoded_pixels', 'region coordinate space required')
        box = region.get('box')
        require(box is None or isinstance(box, list) and len(box) == 4 and all(type(v) is int and v >= 0 for v in box)
                and box[2] > box[0] and box[3] > box[1], 'invalid region pixel box')


class AnnotationWindow:
    def __init__(self, session, annotator):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.session, self.annotator = session, annotator
        self.root = tk.Tk(); self.root.title('Local blind technical annotation · no predictions')
        self.root.geometry('1280x900')
        self.ids = list(session.rows); self.index = 0; self.image = None
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.status = tk.StringVar()
        ttk.Label(self.root, textvariable=self.status, wraplength=1200).pack(fill='x')
        frames = ttk.Frame(self.root); frames.pack(fill='both', expand=True)
        self.full = tk.Canvas(frames, width=600, height=420, background='#222'); self.full.pack(side='left', fill='both', expand=True)
        self.detail = tk.Canvas(frames, width=600, height=420, background='#222'); self.detail.pack(side='right', fill='both', expand=True)
        self.full.bind('<Button-1>', self.crop)
        self.values = {}
        form = ttk.Frame(self.root); form.pack(fill='x')
        choices = {'technical_label': TECHNICAL, 'culling_label': ('K', 'D', 'U', 'X'),
                   'evidence_adequacy': ADEQUACY, 'final_selection': ('keep', 'reject', 'undecided')}
        for i, (key, opts) in enumerate(choices.items()):
            ttk.Label(form, text=key).grid(row=0, column=i)
            var = tk.StringVar(); self.values[key] = var
            ttk.Combobox(form, textvariable=var, values=('', *opts), state='readonly', width=22).grid(row=1, column=i)
        for i, key in enumerate(('reason', 'exclusion_reason', 'aesthetic_preference')):
            ttk.Label(form, text=key).grid(row=2, column=i)
            var = tk.StringVar(); self.values[key] = var
            ttk.Entry(form, textvariable=var, width=30).grid(row=3, column=i)
        ttk.Label(self.root, text='Regions JSON: role=left_eye/right_eye/face_core/required_body/landscape_subject/texture; '
                  'visibility=visible/not_visible/insufficient_pixels/unjudgeable. These are quality observations, NOT subject confirmation.').pack(fill='x')
        self.regions = tk.Text(self.root, height=6); self.regions.pack(fill='x')
        buttons = ttk.Frame(self.root); buttons.pack(fill='x')
        for label, callback in [('Previous', lambda: self.navigate(-1)), ('Next', lambda: self.navigate(1)),
                                ('Save draft', lambda: self.save(False)), ('Submit blind opinion', lambda: self.save(True)),
                                ('Withdraw own submission', self.withdraw), ('Add region template', self.region_template), ('Adjudicate submitted opinions', self.adjudicate),
                                ('Export submitted labels', self.export)]:
            ttk.Button(buttons, text=label, command=callback).pack(side='left')
        self.load()

    def error(self, exc):
        from tkinter import messagebox
        messagebox.showerror('Not saved / input needs attention', str(exc), parent=self.root)

    def load(self):
        from PIL import ImageTk
        aid = self.ids[self.index]
        if self.image is not None:
            self.image.close(); self.image = None
        self.full.delete('all'); self.detail.delete('all')
        self.native_photo = None; self.fit_photo = None
        for var in self.values.values(): var.set('')
        self.regions.delete('1.0', 'end'); self.regions.insert('1.0', '[]')
        old = self.session.latest(aid, self.annotator)
        if old:
            for k, v in self.values.items(): v.set(old.get(k) or '')
            self.regions.delete('1.0', 'end'); self.regions.insert('1.0', json.dumps(old.get('regions', []), ensure_ascii=False, indent=2))
        try:
            self.image, info = self.session.open_image(aid)
            self.scale = min(600 / self.image.width, 420 / self.image.height, 1.0)
            preview = self.image.resize((max(1, round(self.image.width*self.scale)), max(1, round(self.image.height*self.scale))))
            self.fit_photo = ImageTk.PhotoImage(preview); preview.close()
            self.full.create_image(0, 0, image=self.fit_photo, anchor='nw')
            self.status.set(f'{self.index+1}/{len(self.ids)} · {aid} · source={info["source_id"]} '
                            f'· {info["decode"].get("source_kind")} · native={self.image.size} '
                            f'· warnings={info["decode"].get("warnings", [])} · oriented native pixels · full view={self.scale*100:.1f}% · click full image for native 100% detail '
                            f'· guideline={self.session.samples["guideline_version"]} · annotator={self.annotator} '
                            f'· effective submission={bool(self.session.effective_submission(aid, self.annotator))}')
            self.show_crop(self.image.width//2, self.image.height//2)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(f'{aid}: unable to inspect source; no technical truth inferred'); self.error(exc)

    def show_crop(self, x, y):
        from PIL import ImageTk
        left=max(0, min(x-300, self.image.width-600)); top=max(0, min(y-210, self.image.height-420))
        self.box=[left, top, min(left+600,self.image.width), min(top+420,self.image.height)]
        crop=self.image.crop(tuple(self.box)); self.native_photo=ImageTk.PhotoImage(crop); crop.close()
        self.detail.delete('all'); self.detail.create_image(0,0,image=self.native_photo,anchor='nw')
        self.detail.create_text(5,5,text=f'100% · box {self.box}',fill='#ffcc00',anchor='nw')

    def crop(self, event):
        if self.image is not None: self.show_crop(round(event.x/self.scale), round(event.y/self.scale))

    def region_template(self):
        try:
            values=json.loads(self.regions.get('1.0','end'))
            values.append({'region_id': f'region-{len(values)+1}', 'role': '', 'required': None,
                           'visibility': '', 'technical_label': '', 'evidence_adequacy': '',
                           'coordinate_space':'oriented_native_decoded_pixels', 'box':getattr(self,'box',None), 'reason':''})
            self.regions.delete('1.0','end'); self.regions.insert('1.0',json.dumps(values,ensure_ascii=False,indent=2))
        except (ValueError, TypeError, AttributeError) as exc: self.error(exc)

    def save(self, submit):
        try:
            opinion={k:v.get() or None for k,v in self.values.items()}
            for key in ('reason','exclusion_reason','aesthetic_preference'): opinion[key]=opinion[key] or ''
            opinion['regions']=json.loads(self.regions.get('1.0','end'))
            self.session.save(self.ids[self.index],self.annotator,opinion,submit=submit)
            self.status.set('Saved submitted opinion' if submit else 'Saved draft; earlier submitted opinion remains effective until resubmission or explicit withdrawal')
            return True
        except (OSError,ValueError,RuntimeError) as exc: self.error(exc); return False

    def withdraw(self):
        from tkinter import simpledialog
        reason = simpledialog.askstring('Withdraw own submitted opinion',
                 'This removes your effective opinion from future exports and invalidates its adjudication. '
                 'History is retained. Enter a reason; Cancel makes no change.', parent=self.root)
        if not reason: return
        try:
            self.session.withdraw(self.ids[self.index], self.annotator, reason)
            self.status.set('Submission explicitly withdrawn; history and drafts retained')
        except (OSError, ValueError, RuntimeError) as exc: self.error(exc)

    def adjudicate(self):
        from tkinter import messagebox, simpledialog
        aid = self.ids[self.index]
        own = self.session.effective_submission(aid, self.annotator)
        if own is None or own['status'] != 'submitted':
            self.error(ValueError('Submit your own blind opinion before revealing other opinions.')); return
        ratings = self.session._submitted(aid)
        if len(ratings) < 2:
            self.error(ValueError('At least two identified submitted raters required.')); return
        messagebox.showinfo('Submitted opinions (post-blind comparison)',
                            json.dumps(ratings, ensure_ascii=False, indent=2), parent=self.root)
        reason = simpledialog.askstring('Explicit adjudication',
                 'The current form will be the adjudicated opinion. Enter your reason; Cancel makes no change.', parent=self.root)
        if not reason: return
        try:
            opinion = {k: v.get() or None for k, v in self.values.items()}
            for key in ('reason', 'exclusion_reason', 'aesthetic_preference'): opinion[key] = opinion[key] or ''
            opinion['regions'] = json.loads(self.regions.get('1.0', 'end'))
            self.session.adjudicate(aid, self.annotator, opinion, reason)
            self.status.set('Adjudication saved; raw opinions preserved; no expert-consensus claim')
        except (OSError, ValueError, RuntimeError) as exc: self.error(exc)

    def navigate(self, direction):
        from tkinter import messagebox
        if not messagebox.askyesno('Leave this image?', 'Unsaved edits will be discarded. Continue?', parent=self.root): return
        self.index=max(0,min(len(self.ids)-1,self.index+direction)); self.load()

    def export(self):
        from tkinter import filedialog
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',title='New submitted annotation export (do not overwrite)')
        if path:
            try: self.session.export(path); self.status.set('Submitted labels exported; no claim of expert consensus')
            except (OSError,ValueError,RuntimeError) as exc: self.error(exc)

    def close(self):
        from tkinter import messagebox
        if not messagebox.askyesno('Close blind annotation?', 'Unsaved edits will be discarded. Close?', parent=self.root): return
        if self.image is not None: self.image.close()
        self.root.destroy()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=Path,required=True)
    parser.add_argument('--paths',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='new isolated session directory, or existing own session with --resume')
    parser.add_argument('--annotator',required=True)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--export',type=Path,help='export already submitted labels without opening GUI; new file')
    args=parser.parse_args(argv)
    try:
        session=AnnotationSession(oe.read_json(args.samples),oe.read_json(args.paths),args.output,resume=args.resume)
        require(bool(session.rows),'empty sample manifest')
        require(bool(args.annotator.strip()),'annotator ID required')
        if args.export: session.export(args.export)
        else: AnnotationWindow(session,args.annotator).root.mainloop()
        return 0
    except (OSError,ValueError,RuntimeError) as exc:
        print(f'Blind annotation refused: {exc}',file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
