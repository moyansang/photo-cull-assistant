"""Reproducible crop-oracle ranking + untouched pipeline smoke evaluation.

Credit: AT&T Laboratories Cambridge / Olivetti Research Laboratory.
No training, production edits, data upload, or automatic download.
See README.md for what this deliberately limited benchmark can establish.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import random
import subprocess
import sys
import time
from unittest.mock import patch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--data', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=20261008)
    ap.add_argument('--partition', choices=('validation','test','all'), default='test')
    args = ap.parse_args()
    subject_ids = list(range(1,21) if args.partition == 'validation' else range(21,41) if args.partition == 'test' else range(1,41))
    sys.path.insert(0, str(args.repo.resolve() / 'src'))
    import cv2
    import numpy as np
    import PIL
    from PIL import Image, ImageDraw
    from ai_cull_assistant import person_match as pm, body_focus, yunet
    cv2.setNumThreads(1)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    evidence = out / 'evidence'
    evidence.mkdir(exist_ok=True)
    def save(name, obj):
        (out / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    started = time.perf_counter()
    rng = random.Random(args.seed)
    full = (0., 0., 1., 1.)
    images, manifest, splits = {}, {}, {}
    for sid in subject_ids:
        keys = [f's{sid}/{i}.pgm' for i in range(1, 11)]
        for key in keys:
            path = args.data / key
            with Image.open(path) as im:
                assert im.size == (92, 112) and im.mode == 'L', (key, im.size, im.mode)
                images[key] = np.asarray(im.convert('RGB'))
            manifest[key] = sha(path)
        rng.shuffle(keys)
        splits[str(sid)] = {'references': keys[:3], 'queries': keys[3:]}
    refkeys = {k for s in splits.values() for k in s['references']}
    querykeys = {k for s in splits.values() for k in s['queries']}
    assert not refkeys & querykeys
    assert not {manifest[k] for k in refkeys} & {manifest[k] for k in querykeys}, 'byte-identical leakage'
    save('split.json', {'seed': args.seed, 'subjects': splits, 'files_sha256': manifest})
    references = {sid: [pm.build_reference(images[k], full) for k in split['references']]
                  for sid, split in splits.items()}
    episodes = []
    for sid, split in splits.items():
        others = [s for s in splits if s != sid]
        for qi, query in enumerate(split['queries']):
            for present in (True, False):
                distractors = rng.sample(others, 3 if present else 4)
                keys = ([query] if present else []) + [rng.choice(splits[s]['queries']) for s in distractors]
                rng.shuffle(keys)
                episodes.append({'id': f's{sid}_q{qi}_{"present" if present else "absent"}',
                                 'subject': sid, 'present': present, 'query': query, 'candidates': keys})
    save('episodes.json', episodes)
    # Each fixed box is a known entire crop, not a manually annotated anatomical face.
    # The image is a 4-cell gallery solely to host non-overlapping ranking geometry.
    boxes = [(i / 4, 0., .25, 1.) for i in range(4)]
    anchors = [pm._Anchor(b, 'face', 1.) for b in boxes]
    original_appearance = pm._appearance_similarity
    original_detector = pm._detector_anchors
    original_template = pm._template_anchors
    records = []
    saved = Counter()
    def transformed(key, scene):
        arr = images[key].copy()
        if scene == 'synthetic_lower30_mask':
            arr[round(arr.shape[0] * .70):, :] = 128
        return arr
    def picture(ep, scene, nref, ranked, status, crops):
        # Selected failures only; complete failure IDs and scores remain in JSONL.
        canvas = Image.new('RGB', (760, 320), 'white')
        draw = ImageDraw.Draw(canvas)
        draw.text((8, 5), f'{ep["id"]} {scene} refs={nref} {status}', fill='black')
        for j, k in enumerate(splits[ep['subject']]['references'][:nref]):
            canvas.paste(Image.fromarray(images[k]), (8 + j * 110, 35))
            draw.text((8 + j * 110, 150), f'REF {k}', fill='black')
        order = {boxes.index(tuple(c.box)): idx + 1 for idx, c in enumerate(ranked)}
        for j, (k, crop) in enumerate(zip(ep['candidates'], crops)):
            x = 8 + j * 185
            canvas.paste(Image.fromarray(crop), (x, 182))
            is_target = k.split('/')[0] == 's' + ep['subject']
            draw.text((x, 298), f'{k} rank={order.get(j, "none")} GT={is_target}', fill='green' if is_target else 'black')
        name = f'{scene}_{nref}_{ep["id"]}.png'
        canvas.save(evidence / name)
        return 'evidence/' + name
    for scene in ('original', 'synthetic_lower30_mask'):
        for ep in episodes:
            crops = [transformed(k, scene) for k in ep['candidates']]
            gallery = np.concatenate(crops, axis=1)
            def appearance(_image, reference, anchor):
                # Keep descriptors clipped to their original crop, avoiding accidental
                # head/upper-region inclusion of an adjacent gallery identity.
                crop = crops[boxes.index(tuple(anchor.box))]
                return original_appearance(crop, reference, pm._Anchor(full, 'face', 1.))
            for nref in (1, 3):
                t0 = time.perf_counter()
                with patch.object(pm, '_detector_anchors', return_value=anchors), \
                     patch.object(pm, '_template_anchors', return_value=[]), \
                     patch.object(pm, '_appearance_similarity', side_effect=appearance):
                    ranked = pm.rank_candidates_multi(gallery, references[ep['subject']][:nref])
                elapsed = time.perf_counter() - t0
                ranked_keys = [ep['candidates'][boxes.index(tuple(c.box))] for c in ranked]
                target_rank = next((i + 1 for i, k in enumerate(ranked_keys) if k.split('/')[0] == 's' + ep['subject']), None)
                status = ('correct' if target_rank == 1 else 'miss' if not ranked else 'wrong') if ep['present'] else ('false_proposal' if ranked else 'correct_reject')
                rec = {**ep, 'scene': scene, 'references': nref, 'ranked_keys': ranked_keys,
                       'ranked_candidates': [asdict(c) for c in ranked], 'target_rank': target_rank,
                       'status': status, 'seconds': elapsed,
                       'requires_explicit_candidate_choice': bool(ranked and (ranked[0].ambiguous or getattr(ranked[0], 'review_reasons', ()))),
                       'top_tie_1e12': bool(len(ranked) > 1 and abs(ranked[0].score-ranked[1].score) <= 1e-12)}
                group = (scene, nref, status)
                if status in ('wrong', 'miss', 'false_proposal') and saved[group] < 3:
                    rec['evidence'] = picture(ep, scene, nref, ranked, status, crops)
                    saved[group] += 1
                records.append(rec)
        print(f'Finished oracle scenario: {scene}', flush=True)
    assert pm._appearance_similarity is original_appearance
    assert pm._detector_anchors is original_detector and pm._template_anchors is original_template
    (out / 'trials.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
    failures = [r for r in records if r['status'] in ('wrong', 'miss', 'false_proposal')]
    (out / 'failures.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in failures), encoding='utf-8')
    groups = defaultdict(list)
    for rec in records:
        groups[(rec['scene'], rec['references'])].append(rec)
    summary = []
    for (scene, nref), rows in groups.items():
        pos = [r for r in rows if r['present']]
        neg = [r for r in rows if not r['present']]
        summary.append({'scene': scene, 'references': nref, 'present_n': len(pos), 'absent_n': len(neg),
            'top1_correct': sum(r['target_rank'] == 1 for r in pos),
            'top2_correct': sum(r['target_rank'] is not None and r['target_rank'] <= 2 for r in pos),
            'top3_correct': sum(r['target_rank'] is not None and r['target_rank'] <= 3 for r in pos),
            'top4_correct': sum(r['target_rank'] is not None and r['target_rank'] <= 4 for r in pos),
            'present_empty_misses': sum(not r['ranked_keys'] for r in pos),
            'present_wrong_top1': sum(r['status'] == 'wrong' for r in pos),
            'target_missing_from_returned_list': sum(r['target_rank'] is None for r in pos),
            'absent_false_proposals': sum(bool(r['ranked_keys']) for r in neg),
            'absent_correct_rejects': sum(not r['ranked_keys'] for r in neg),
            'ambiguous_top1': sum(bool(r['ranked_candidates'] and r['ranked_candidates'][0]['ambiguous']) for r in rows),
            'top_ties_1e12': sum(r['top_tie_1e12'] for r in rows),
            'requires_explicit_candidate_choice': sum(r['requires_explicit_candidate_choice'] for r in rows),
            'seconds': sum(r['seconds'] for r in rows)})
    # Untouched production pipeline on single held-out images: execution/output
    # smoke metrics only, because ORL has no anatomical face-box ground truth.
    model_status = {}
    try:
        paths = body_focus.find_body_models()
        model_status['body_models'] = [{'path': str(p), 'sha256': sha(p)} for p in paths]
    except Exception as exc:
        model_status['body_models_error'] = str(exc)
    model_status['yunet'] = {'path': str(yunet.MODEL_PATH), 'sha256': sha(yunet.MODEL_PATH)}
    smoke = []
    diagnostics = []
    for sid, split in splits.items():
        keys = [(True, split['queries'][0]), (False, splits[str(subject_ids[(subject_ids.index(int(sid))+1) % len(subject_ids)])]['queries'][0])]
        for present, key in keys:
            bgr = cv2.cvtColor(images[key], cv2.COLOR_RGB2BGR)
            try:
                faces = yunet.detect(bgr, .8, rotate_rescue=False)
                diag = {'image': key, 'faces': len(faces)}
            except Exception as exc:
                diag = {'image': key, 'detector_error': repr(exc)}
            diagnostics.append(diag)
            for nref in (1, 3):
                t0 = time.perf_counter()
                try:
                    ranked = pm.rank_candidates_multi(images[key], references[sid][:nref])
                    smoke.append({'subject': sid, 'image': key, 'present': present, 'references': nref,
                                  'seconds': time.perf_counter()-t0, 'candidates': [asdict(c) for c in ranked]})
                except Exception as exc:
                    smoke.append({'subject': sid, 'image': key, 'present': present, 'references': nref,
                                  'seconds': time.perf_counter()-t0, 'error': repr(exc)})
    save('pipeline_smoke.json', {'models': model_status, 'yunet_diagnostics': diagnostics, 'trials': smoke})
    smoke_summary = []
    for nref in (1, 3):
        for present in (True, False):
            rows = [r for r in smoke if r['references'] == nref and r['present'] == present]
            smoke_summary.append({'references': nref, 'present': present, 'n': len(rows),
                                  'errors': sum('error' in r for r in rows),
                                  'nonempty': sum(bool(r.get('candidates')) for r in rows),
                                  'top1_sources': dict(Counter(r['candidates'][0]['source'] for r in rows if r.get('candidates'))),
                                  'seconds': sum(r['seconds'] for r in rows)})
    result = {'seed': args.seed, 'commit': subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', 'HEAD'], text=True).strip(),
              'source_sha256': sha(Path(pm.__file__)), 'module_file': pm.__file__, 'python': sys.version, 'executable': sys.executable,
              'platform': platform.platform(), 'versions': {'numpy': np.__version__, 'opencv': cv2.__version__, 'Pillow': PIL.__version__},
              'opencv_threads': cv2.getNumThreads(), 'subjects': len(subject_ids), 'partition': args.partition, 'reference_files': len(refkeys), 'query_files': len(querykeys),
              'unique_file_hashes': len(set(manifest.values())), 'data_zip_sha256': sha(args.data.parent / 'att_faces.zip') if (args.data.parent / 'att_faces.zip').is_file() else None,
              'tie_policy': 'Exact scores retain randomized input order (Python stable sort); near ties are flagged by production margin .065, never rejected.',
              'oracle': summary, 'pipeline_smoke': smoke_summary, 'wall_seconds': time.perf_counter()-started}
    save('summary.json', result)
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
