from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

import pipeline
from test_hybrid import clear
from ai_cull_assistant.shared_decode import full_image, shared_decode


@pytest.mark.parametrize('slots',[1,2])
def test_gpu_slots_isolate_buffers(tmp_path,slots):
    pytest.importorskip('pyopencl')
    pool=pipeline.DeveloperPool(tmp_path,slots)
    pattern=np.array([0,1,3,2],np.int32)
    params=np.r_[np.zeros(4),np.ones(4)/4095,np.eye(3).ravel()].astype(np.float32)
    arrays=[np.full((256,256),v,np.uint16) for v in [200,800,1500,3200]]
    try:
        expected=[pool.run(a,pattern,params)[0] for a in arrays]
        gate=Barrier(4)
        def work(i):
            gate.wait();return pool.run(arrays[i],pattern,params)[0]
        with ThreadPoolExecutor(max_workers=4) as ex:actual=list(ex.map(work,range(4)))
        for a,b in zip(actual,expected):np.testing.assert_array_equal(a,b)
        assert len({id(b.context) for b in pool.backends})==1
        assert len({id(b.queue) for b in pool.backends})==slots
        assert pool.summary()['peak_active']<=slots
        # An invalid photo must return its lease so later photos can run.
        with pytest.raises(ValueError):pool.run(np.zeros((2,2),np.uint16),pattern,params)
        np.testing.assert_array_equal(pool.run(arrays[0],pattern,params)[0],expected[0])
    finally:pool.close()


def test_cpu_review_mode_is_thread_local():
    barrier=Barrier(2);seen=[]
    concurrent=pipeline.ConcurrentHybrid(lambda _:Image.new('RGB',(2,2),'lime'))
    def assess(asset,**kwargs):
        assert kwargs['cache_dir'] is None
        with full_image(asset) as image:pixel=image.getpixel((0,0))
        if pixel==(255,0,0):
            barrier.wait(timeout=5)
            with full_image(asset) as image:assert image.getpixel((0,0))==(255,0,0)
        else:
            # Both GPU analyses meet before either thread starts CPU review.
            barrier.wait(timeout=5)
        seen.append(pixel)
        result=clear()
        result.focus_evidence['regions']['face_core']['fine']['laplacian_normalized']=.0064
        return result
    with patch.object(pipeline,'assess_asset_focus',side_effect=assess), \
         patch.object(pipeline,'load_full_image',side_effect=lambda _:Image.new('RGB',(2,2),'red')), \
         patch('ai_cull_assistant.face_focus.load_full_image',concurrent.load):
        def work(_):
            asset=object()
            with shared_decode(asset):return concurrent.assess(asset,cache_dir='unused')
        with ThreadPoolExecutor(max_workers=2) as ex:results=list(ex.map(work,range(2)))
    assert seen.count((255,0,0))==2 and seen.count((0,255,0))==2
    assert all(r.focus_evidence['hybrid']['cpu_reviewed'] for r in results)


def test_one_cpu_thread_does_not_change_other_gpu_thread():
    concurrent=pipeline.ConcurrentHybrid(lambda _:Image.new('RGB',(2,2),'lime'))
    entered=Event();finished=Event()
    def cpu_thread():
        concurrent.state.cpu=True;entered.set()
        assert finished.wait(5)
        with concurrent.load(object()) as im:assert im.getpixel((0,0))==(255,0,0)
    def gpu_thread():
        assert entered.wait(5)
        try:
            with concurrent.load(object()) as im:assert im.getpixel((0,0))==(0,255,0)
        finally:finished.set()
    with patch.object(pipeline,'load_full_image',side_effect=lambda _:Image.new('RGB',(2,2),'red')):
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures=[ex.submit(cpu_thread),ex.submit(gpu_thread)]
            for future in futures:future.result()
