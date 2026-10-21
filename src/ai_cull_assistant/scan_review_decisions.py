"""Scan-results adapter for the existing ReviewProject human decisions.

Reading does not create a project, reconcile an archive, decode a photograph,
measure quality or request AI. Mutations are explicit and use the existing
project/export format; feedback is deliberately not handled here.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from .ai_project import ReviewProject, photo_id
from .evidence_cleanup import evidence_operation

UNSET = object()


class DecisionConflict(ValueError):
    """The displayed project or source changed; reload before applying an edit."""


def _revision(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class ReviewDecisions:
    def __init__(self, workspace, assets, crop_settings):
        self.workspace = Path(workspace).resolve()
        self.assets = list(assets)
        self.crop_settings = crop_settings
        self.path = self.workspace / 'ai_project.json'
        self._seen_revision = UNSET
        self._seen_rows = {}

    def _project(self):
        project = ReviewProject(self.workspace, read_only=True)
        # refresh rebuilds scopes on assets, so never give it live UI objects.
        project.refresh(copy.deepcopy(self.assets), copy.deepcopy(self.crop_settings), persist=False)
        return project

    def rows(self):
        before = _revision(self.path)
        project = self._project()
        after = _revision(self.path)
        if before != after:
            raise DecisionConflict('选片记录正在变化，请刷新后再查看。')
        self._seen_revision = after
        self._seen_rows = copy.deepcopy(project.data['photos'])
        return copy.deepcopy(self._seen_rows)

    def _mutate(self, asset, operation):
        if not any(item is asset for item in self.assets):
            raise DecisionConflict('照片不属于当前结果列表，请重新打开。')
        if self._seen_revision is UNSET:
            self.rows()
        with evidence_operation(self.workspace):
            if _revision(self.path) != self._seen_revision:
                raise DecisionConflict('人工或 AI 记录已变化；未覆盖，请刷新后再操作。')
            project = self._project()
            pid = photo_id(asset)
            row = project.data['photos'][pid]
            prior = self._seen_rows.get(pid)
            if not prior or row['fingerprint'] != prior['fingerprint']:
                raise DecisionConflict('照片、范围或分组已变化；未应用旧页面操作，请刷新。')
            operation(project, pid)
            self._seen_revision = _revision(self.path)
            self._seen_rows = copy.deepcopy(project.data['photos'])
            return copy.deepcopy(project.data['photos'][pid])

    def set(self, asset, *, pick_status=UNSET, rating=UNSET):
        """Set explicit fields, preserving other displayed effective fields.

        ``rating=0`` explicitly clears Lightroom stars; ``None`` means do not
        overwrite that Lightroom field. An omitted field is retained. Setting
        stars alone does not invent a keep/reject choice when it was unknown.
        """
        if pick_status is UNSET and rating is UNSET:
            raise ValueError('请选择人工留弃或星级。')

        def apply(project, pid):
            current = project.data['photos'][pid].get('final', {})
            confirmed = bool(current.get('confirmed'))
            project.confirm(pid, current.get('rating') if rating is UNSET else rating,
                            current.get('pick_status') if pick_status is UNSET else pick_status,
                            pick_confirmed=(pick_status is not UNSET or
                                            (confirmed and current.get('pick_confirmed', True))),
                            rating_confirmed=(rating is not UNSET or
                                              (confirmed and current.get('rating_confirmed', True))))
        return self._mutate(asset, apply)

    def withdraw(self, asset):
        return self._mutate(asset, lambda project, pid: project.withdraw_confirmation(pid))

    def export(self):
        """Export only on explicit user action, with existing LR validation."""
        if self._seen_revision is UNSET:
            self.rows()
        with evidence_operation(self.workspace):
            if _revision(self.path) != self._seen_revision:
                raise DecisionConflict('选片记录已变化，请刷新后再导出。')
            project = self._project()
            if any(self._seen_rows.get(pid, {}).get('fingerprint') != row['fingerprint']
                   for pid, row in project.data['photos'].items()):
                raise DecisionConflict('照片、范围或分组已变化；未导出，请刷新后核对。')
            # Export refreshes the copied inputs, and writes only workspace JSON.
            target = project.export_final()
            self._seen_revision = _revision(self.path)
            self._seen_rows = copy.deepcopy(project.data['photos'])
            return target
