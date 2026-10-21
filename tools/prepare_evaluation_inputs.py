#!/usr/bin/env python3
"""Prepare new evaluation JSON from explicit legacy bindings; never open photos.

This metadata-only adapter neither measures images nor grants permission. It
uses lexical path operations, not filesystem discovery or source hashing.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

import evaluation_inputs as inputs
import offline_evaluation as oe

LEGACY_SCHEMA = 'sample-bindings-v1'
LOCAL_ROOTS_SCHEMA = 'sample-local-roots-v1'
REGISTRATION_SCHEMA = 'evaluation-input-registration-v1'
SOURCE_FIELDS = {'dataset_id', 'dataset_version', 'original_file_id', 'original_sha256',
                 'preprocessing_history', 'width', 'height'}
TOP_FIELDS = {'dataset_registry', 'registry_review', 'split_registry', 'guideline_review'}
ROLES = {'primary_source_id', 'raw_source_id', 'jpeg_source_id'}


def require(value, message):
    oe.require(value, message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def keys(value, allowed, name):
    require(isinstance(value, dict), name + ' must be an object')
    require(not (value.keys() - allowed), name + ': unsupported fields: ' + ', '.join(sorted(value.keys() - allowed)))


def lexical_path(root, relative):
    """Accept explicit absolute local roots, never resolve symlinks or stat them."""
    require(text(root) and '\x00' not in root, 'invalid root')
    windows = PureWindowsPath(root)
    path_type = PureWindowsPath if windows.is_absolute() else PurePosixPath
    base = path_type(root)
    require(base.is_absolute(), 'root must be absolute')
    require(text(relative) and '\x00' not in relative, 'relative_path required')
    # Check both grammars so a POSIX conversion cannot hide Windows traversal.
    for part in (PureWindowsPath(relative), PurePosixPath(relative)):
        require(not part.is_absolute() and not part.drive and not part.root and '..' not in part.parts,
                'relative_path must not be absolute or traverse a root')
    require(relative not in ('.', ''), 'relative_path must name a member')
    return str(base / path_type(relative))


def normalize_roots(document):
    """Unwrap only the explicit acquisition version; never inspect root files."""
    require(isinstance(document, dict), 'roots must be root_id to absolute path object')
    if 'schema_version' in document:
        keys(document, {'schema_version', 'roots'}, 'local roots')
        require(document['schema_version'] == LOCAL_ROOTS_SCHEMA, 'unsupported local roots schema')
        document = document.get('roots')
        require(isinstance(document, dict), 'local roots.roots must be an object')
    result = deepcopy(document)
    for rid, value in result.items():
        require(text(rid), 'root_id required')
        lexical_path(value, 'metadata-only-placeholder')
    return result


def identity_check(entry, source, aid, sid):
    """Compare declared metadata only, preserving missing values as unverified."""
    checks = {}
    for field in ('sha256', 'bytes'):
        bound, declared = entry.get(field), source.get(field)
        for value in (bound, declared):
            if value is None:
                continue
            if field == 'sha256':
                require(isinstance(value, str) and len(value) == 64 and
                        all(c in '0123456789abcdefABCDEF' for c in value),
                        aid + '/' + sid + ': invalid sha256')
            else:
                require(type(value) is int and value >= 0, aid + '/' + sid + ': invalid bytes')
        if bound is not None and declared is not None:
            equal = bound.lower() == declared.lower() if field == 'sha256' else bound == declared
            require(equal, aid + '/' + sid + ': binding ' + field + ' mismatch')
            status = 'metadata_match'
        else:
            status = 'unverified'
        checks[field] = {'status': status, 'binding_value': bound, 'manifest_value': declared,
                         'binding_field_present': field in entry, 'manifest_field_present': field in source,
                         'missing': [origin for origin, value in [('binding', bound), ('manifest', declared)] if value is None]}
    return {'asset_id': aid, 'source_id': sid, 'checks': checks,
            'file_identity': 'not_verified_no_photo_access'}


def add_missing(target, additions, allowed, name):
    keys(additions, allowed, name)
    for key, value in additions.items():
        require(key not in target or target[key] == value, name + ': cannot replace existing ' + key)
        target[key] = deepcopy(value)


def prepare(samples, legacy, *, roots=None, registration=None):
    """Return (new samples, path map, gaps report), leaving inputs untouched.

    The supported legacy shape is deliberately explicit, not inferred from
    filenames: entries is a list of asset_id/source_id/root_id/relative_path.
    Unknown legacy/registration fields fail rather than silently losing data.
    """
    keys(legacy, {'schema_version', 'manifest_sha256', 'roots', 'entries', 'note'}, 'legacy bindings')
    require(legacy.get('schema_version') == LEGACY_SCHEMA, 'unsupported legacy bindings schema')
    if 'note' in legacy:
        require(isinstance(legacy['note'], str), 'legacy note must be a string')
    if 'manifest_sha256' in legacy:
        require(legacy['manifest_sha256'] == oe.canonical_hash(samples), 'legacy manifest_sha256 mismatch')
    require(isinstance(samples, dict) and samples.get('schema_version') == oe.SCHEMA and
            samples.get('kind') == 'samples' and isinstance(samples.get('rows'), list), 'unsupported samples schema/kind/rows')
    result = deepcopy(samples)
    rows = oe.unique_rows(result['rows'], 'samples')
    registration = deepcopy(registration or {'schema_version': REGISTRATION_SCHEMA})
    keys(registration, {'schema_version', 'manifest_sha256', 'sample_additions', 'source_additions',
                        'manifest_additions', 'member_roles', 'measurement_authorizations'}, 'registration')
    require(registration.get('schema_version') == REGISTRATION_SCHEMA, 'unsupported registration schema')
    if 'manifest_sha256' in registration:
        require(registration['manifest_sha256'] == oe.canonical_hash(samples), 'registration manifest_sha256 mismatch')
    add_missing(result, registration.get('manifest_additions', {}), TOP_FIELDS, 'manifest additions')
    source_additions = registration.get('source_additions', {})
    sample_additions = registration.get('sample_additions', {})
    roles = registration.get('member_roles', {})
    for name, value in [('source_additions', source_additions), ('sample_additions', sample_additions), ('member_roles', roles)]:
        keys(value, set(rows), name)
    for aid, row in rows.items():
        add_missing(row, sample_additions.get(aid, {}), {'session_id', 'burst_id'}, 'sample additions ' + aid)
        sources = {}
        require(isinstance(row.get('sources'), list), aid + ': sources required')
        for source in row['sources']:
            require(isinstance(source, dict) and text(source.get('source_id')), aid + ': source_id required')
            require(source['source_id'] not in sources, aid + ': duplicate source_id')
            sources[source['source_id']] = source
        additions = source_additions.get(aid, {})
        keys(additions, set(sources), aid + ': source additions')
        for sid, value in additions.items():
            add_missing(sources[sid], value, SOURCE_FIELDS, aid + '/' + sid)
        keys(roles.get(aid, {}), ROLES, aid + ': member roles')
    roots_document = legacy.get('roots', {}) if roots is None else roots
    local_roots = normalize_roots(roots_document)
    entries = legacy.get('entries')
    require(isinstance(entries, list), 'legacy entries must be an array')
    mapped, seen, identity_checks = {}, set(), []
    for entry in entries:
        keys(entry, {'asset_id', 'source_id', 'root_id', 'root', 'relative_path', 'sha256', 'bytes'}, 'legacy entry')
        if 'root_id' in entry and 'root' in entry:
            require(entry['root_id'] == entry['root'], 'entry root and root_id conflict')
        aid, sid, rid = (entry.get(k) for k in ('asset_id', 'source_id', 'root_id'))
        rid = entry.get('root_id', entry.get('root'))
        require(text(aid) and aid in rows, 'entry asset_id is not listed')
        require(text(sid) and sid in {s['source_id'] for s in rows[aid]['sources']}, 'entry source_id is not listed')
        require(text(rid) and rid in local_roots, 'entry root_id is not registered')
        require((aid, sid) not in seen, 'duplicate source binding')
        seen.add((aid, sid))
        source = next(s for s in rows[aid]['sources'] if s['source_id'] == sid)
        identity_checks.append(identity_check(entry, source, aid, sid))
        mapped.setdefault(aid, {})[sid] = lexical_path(local_roots[rid], entry.get('relative_path'))
    # Reject duplicate lexical paths without touching the filesystem. Native
    # preflight will additionally check symlink/real-path aliasing on that host.
    identities = set()
    for members in mapped.values():
        for value in members.values():
            ident = str(PureWindowsPath(value)).casefold() if PureWindowsPath(value).is_absolute() else value
            require(ident not in identities, 'source path reused')
            identities.add(ident)
    registry, global_gaps = inputs._registry(result)
    if not text(result.get('registry_review')):
        global_gaps.append('registry_review_missing')
    if not isinstance(result.get('split_registry'), list):
        global_gaps.append('split_registry_not_declared')
    review = result.get('guideline_review', {})
    if not (isinstance(review, dict) and review.get('frozen') is True and
            review.get('guideline_version') == result.get('guideline_version') and text(review.get('evidence'))):
        global_gaps.append('guideline_freeze_not_confirmed')
    permissions = registration.get('measurement_authorizations', [])
    require(isinstance(permissions, list), 'measurement_authorizations must be array')
    authorized = set()
    for permission in permissions:
        keys(permission, {'dataset_id', 'dataset_version', 'scope', 'evidence'}, 'measurement authorization')
        identity = (permission.get('dataset_id'), permission.get('dataset_version'))
        require(all(text(v) for v in identity) and identity in registry, 'measurement authorization must match registered dataset pair')
        require(permission.get('scope') == 'measurement' and text(permission.get('evidence')),
                'measurement authorization requires explicit scope and evidence')
        require(identity not in authorized, 'duplicate measurement authorization')
        authorized.add(identity)
    bindings, reports = {}, []
    for aid, sample in rows.items():
        gaps = list(global_gaps)
        members = mapped.get(aid, {})
        expected = {s['source_id'] for s in sample['sources']}
        for sid in sorted(expected - members.keys()):
            gaps.append(sid + ':path_binding_missing')
        for source in sample['sources']:
            sid = source['source_id']
            gaps.extend(sid + ':' + gap for gap in inputs._source_problems(source, registry))
            if (source.get('dataset_id'), source.get('dataset_version')) not in authorized:
                gaps.append(sid + ':explicit_measurement_authorization_missing')
        for field in ('session_id', 'burst_id'):
            if field not in sample:
                gaps.append('relationship_registration_missing:' + field)
        if sample.get('scene_type') in ('unknown', 'mixed'):
            gaps.append('explicit_subject_scope_required_no_scene_inference')
        role = roles.get(aid, {})
        primary = role.get('primary_source_id', sample.get('evaluation_source_id'))
        raw, jpeg = role.get('raw_source_id'), role.get('jpeg_source_id')
        for field, sid in [('primary_source_id', primary), ('raw_source_id', raw), ('jpeg_source_id', jpeg)]:
            require(sid is None or text(sid) and sid in expected, aid + ': invalid ' + field)
        require(raw is None or raw != jpeg, aid + ': member cannot have both roles')
        if {raw, jpeg} - {None} != expected:
            gaps.append('measurement_member_roles_incomplete')
        formats = {s['source_id']: str(s.get('format', '')).upper().lstrip('.') for s in sample['sources']}
        for field, sid, allowed in [('raw_source_id', raw, inputs.RAW_FORMATS),
                                    ('jpeg_source_id', jpeg, inputs.RASTER_FORMATS)]:
            if sid is not None and formats[sid] not in allowed:
                gaps.append('member_role_mismatch:' + field)
        if len(expected) == 2 and (not raw or not jpeg or formats[jpeg] not in {'JPG', 'JPEG'}):
            gaps.append('explicit_raw_jpeg_pair_required')
        if len(expected) > 2:
            gaps.append('more_than_two_measurement_members_unsupported')
        if primary not in members:
            gaps.append('primary_path_not_bound')
        if members.keys() == expected and primary in members:
            bindings[aid] = {'primary_path': members[primary], 'sources': members,
                             'raw_source_id': raw, 'jpeg_source_id': jpeg}
        reports.append({'asset_id': aid, 'status': 'blocked' if gaps else 'metadata_prepared', 'problems': gaps})
    # A registry confirmation used only for download must never become a batch
    # read authorization by copying it into the new manifest. Preserve the old
    # record in the input and report; only the new copy is conservatively gated.
    for identity, entry in registry.items():
        if identity not in authorized and entry.get('authorization_confirmed') is True:
            entry['authorization_confirmed'] = False
    paths = {'schema_version': inputs.PATH_SCHEMA, 'manifest_sha256': oe.canonical_hash(result), 'bindings': bindings}
    report = {'schema_version': 'evaluation-input-preparation-report-v1',
              'input_manifest_sha256': oe.canonical_hash(samples), 'prepared_manifest_sha256': oe.canonical_hash(result),
              'input_bindings_sha256': oe.canonical_hash(legacy), 'roots_sha256': oe.canonical_hash(local_roots), 'registration_sha256': oe.canonical_hash(registration),
              'status': 'blocked' if any(r['problems'] for r in reports) or global_gaps else 'metadata_prepared',
              'sample_count': len(rows), 'bound_sample_count': len(bindings), 'rows': reports,
              'global_problems': global_gaps, 'image_files_read': 0, 'measurement_performed': False,
              'authorization_granted': False, 'preflight': 'not_run', 'native_path_validation': 'not_run',
              'acquisition_provenance': {'bindings': deepcopy(legacy), 'roots_document': deepcopy(roots_document)},
              'identity_checks': identity_checks,
              'manifest_binding_check': 'metadata_match' if 'manifest_sha256' in legacy else 'unverified_missing_binding_hash',
              'note': 'Metadata conversion only. No photos read, labels made, permissions granted, or readiness certified.'}
    return result, paths, report


def run(samples_path, bindings_path, output, *, roots_path=None, registration_path=None):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON field: ' + key)
            result[key] = value
        return result

    def read(path):
        with Path(path).open(encoding='utf-8') as stream:
            return json.load(stream, object_pairs_hook=unique_object)
    samples, legacy = read(samples_path), read(bindings_path)
    roots = read(roots_path) if roots_path else None
    registration = read(registration_path) if registration_path else None
    result, paths, report = prepare(samples, legacy, roots=roots, registration=registration)
    output = oe.outside_repository(Path(output))
    # Exclusive new directory creation protects all prior inputs and outputs.
    require(not output.exists(), 'output already exists')
    actual_roots = normalize_roots(roots if roots is not None else legacy.get('roots', {}))
    for root in actual_roots.values():
        if PureWindowsPath(root).is_absolute():
            require(not PureWindowsPath(str(output)).is_relative_to(PureWindowsPath(root)), 'output overlaps photo root')
        else:
            require(not PurePosixPath(str(output)).is_relative_to(PurePosixPath(root)), 'output overlaps photo root')
    output.mkdir(parents=True, exist_ok=False)
    for name, value in [('samples.json', result), ('paths.json', paths), ('preparation-report.json', report)]:
        oe.write_new(output / name, value)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--bindings', type=Path, required=True)
    parser.add_argument('--roots', type=Path)
    parser.add_argument('--registration', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run(args.samples, args.bindings, args.output, roots_path=args.roots, registration_path=args.registration)
        print(json.dumps({'status': report['status'], 'sample_count': report['sample_count'],
                          'image_files_read': 0}, ensure_ascii=False))
        return 0 if report['status'] == 'metadata_prepared' else 3
    except (OSError, ValueError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
