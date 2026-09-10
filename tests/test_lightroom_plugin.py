from pathlib import Path
import json
import pytest

PLUGIN=Path(__file__).resolve().parents[1]/'lightroom/PhotoCullAssistant.lrplugin'


def test_lua_catalog_import_validation_and_metadata_scope():
    # Run the shipped Lua 5.1 code, using only a fake catalog for SDK calls.
    from lupa.lua51 import LuaRuntime
    lua=LuaRuntime(unpack_returned_tuples=True)
    core=lua.execute((PLUGIN/'Core.lua').read_text('utf-8'))
    decoder=lua.execute((PLUGIN/'json.lua').read_text('utf-8'))
    lua.execute('''
        writes={}
        photos={
          ['/photos/中文.RW2']={ rating=3, pickStatus=0 },
          ['/other/中文.RW2']={ rating=2, pickStatus=1 },
          ['/photos/virtual.RW2']={ rating=1, isVirtualCopy=true }
        }
        for path,p in pairs(photos) do
          function p:getRawMetadata(k) return self[k] end
          function p:setRawMetadata(k,v) self[k]=v; table.insert(writes,k) end
        end
        catalog={findPhotoByPath=function(self,path) return photos[path] end}
    ''')
    data={'format':'photo-cull-assistant','version':1,'photos':[
        {'path':'/photos/中文.RW2','pick_status':-1},
        {'path':'/photos/missing.RW2','rating':4},
        {'path':'/photos/virtual.RW2','rating':5}]}
    rows=core.validate(decoder.decode(json.dumps(data,ensure_ascii=False)))
    matched,missing,report=core.plan(rows,lua.globals().catalog)
    assert len(matched)==1 and len(missing)==2
    assert report[1]['before_rating']==3
    core.apply(matched)
    photos=lua.globals().photos
    assert photos['/photos/中文.RW2']['pickStatus']==-1
    assert photos['/photos/中文.RW2']['rating']==3
    assert photos['/other/中文.RW2']['pickStatus']==1
    assert photos['/photos/virtual.RW2']['rating']==1
    assert list(lua.globals().writes.values())==['pickStatus']
    core.apply(matched)
    assert photos['/photos/中文.RW2']['pickStatus']==-1
    for row in [{'path':'relative.jpg','rating':5},{'path':'/photo.jpg','rating':6},{'path':'/photo.jpg','pick_status':2}]:
        data['photos']=[row]
        with pytest.raises(Exception): core.validate(decoder.decode(json.dumps(data)))
    data['photos']=[{'path':'/photo.jpg','rating':4},{'path':'/photo.jpg','rating':5}]
    with pytest.raises(Exception): core.validate(decoder.decode(json.dumps(data)))
    # Parse the entry point without executing Lightroom imports.
    lua.execute('assert(loadstring(...))',(PLUGIN/'ImportResults.lua').read_text('utf-8'))
    info=lua.execute((PLUGIN/'Info.lua').read_text('utf-8'))
    assert info['LrLibraryMenuItems'][1]['file']=='ImportResults.lua'


@pytest.mark.parametrize('answer,expected', [('ok',5),('cancel',2)])
def test_lua_entrypoint_confirmation_and_catalog_gate(answer,expected):
    from lupa.lua51 import LuaRuntime
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().read_file=lambda path: ((PLUGIN/path.rsplit('/',1)[-1]).read_text('utf-8') if not path.endswith('results.json') else json.dumps({'format':'photo-cull-assistant','version':1,'photos':[{'path':'/照片/a.RW2','rating':5}]}))
    lua.globals().answer=answer
    lua.execute('''
        _PLUGIN={path='/中文插件/PhotoCullAssistant.lrplugin'}
        photo={rating=2,pickStatus=1}
        function photo:getRawMetadata(k) return self[k] end
        function photo:setRawMetadata(k,v) assert(inGate); self[k]=v end
        catalog={}
        function catalog:findPhotoByPath(path) if path=='/照片/a.RW2' then return photo end end
        function catalog:getPath() return '/catalog.lrcat' end
        function catalog:setPropertyForPlugin(plugin,k,v) assert(inGate); report=v end
        function catalog:withPrivateWriteAccessDo(f,timeout) inGate=true; f(); inGate=false; return 'executed' end
        function catalog:withWriteAccessDo(name,f,timeout) inGate=true; f(); inGate=false; return 'executed' end
        local modules={
          LrTasks={startAsyncTask=function(f) f() end,pcall=pcall},
          LrDialogs={runOpenPanel=function() return {'/中文工作区/results.json'} end,
            confirm=function() return answer end,message=function(title,text,kind) lastMessage=title; lastDetail=text end},
          LrApplication={activeCatalog=function() return catalog end},
          LrPathUtils={child=function(a,b) return a..'/'..b end},
          LrFileUtils={readFile=function(path) return read_file(path) end},
        }
        function import(name) return assert(modules[name],name) end
    ''')
    lua.execute((PLUGIN/'ImportResults.lua').read_text('utf-8'))
    assert lua.globals().photo['rating']==expected, lua.globals().lastDetail
    assert lua.globals().photo['pickStatus']==1
    report=json.loads(lua.globals().report)
    assert report['status']==('applied' if answer=='ok' else 'not_applied')
    assert report['changes'][0]['before_rating']==2
    lua.execute('assert(loadstring(...))',(PLUGIN/'ViewReport.lua').read_text('utf-8'))
