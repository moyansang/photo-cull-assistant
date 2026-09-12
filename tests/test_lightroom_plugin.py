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
    matched,missing=core.plan(rows,lua.globals().catalog)
    assert len(matched)==1 and len(missing)==2
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
    assert info['LrMetadataProvider']=='MetadataProvider.lua'
    assert info['LrMetadataTagsetFactory'][1]=='MetadataTagset.lua'


def test_lua_ai_metadata_validation_apply_and_lightroom_schema():
    from lupa.lua51 import LuaRuntime
    lua=LuaRuntime(unpack_returned_tuples=True)
    core=lua.execute((PLUGIN/'Core.lua').read_text('utf-8'))
    decoder=lua.execute((PLUGIN/'json.lua').read_text('utf-8'))
    lua.execute('''
        _PLUGIN={id='com.photocullassistant.catalogimport'}
        pluginWrites={}
        rawWrites={}
        photo={
          rating=4,
          pickStatus=1,
          caption='原说明',
          title='原标题',
          properties={selection_reason='旧理由',clarity_status='旧状态'}
        }
        function photo:getRawMetadata(k) return self[k] end
        function photo:setRawMetadata(k,v) self[k]=v; table.insert(rawWrites,k) end
        function photo:setPropertyForPlugin(plugin,k,v)
            assert(plugin==_PLUGIN)
            self.properties[k]=v
            table.insert(pluginWrites,{field=k,value=v})
        end
        catalog={findPhotoByPath=function(self,path) return photo end}
    ''')

    def apply(row):
        payload={'format':'photo-cull-assistant','version':1,'photos':[{'path':'/photos/a.RW2',**row}]}
        rows=core.validate(decoder.decode(json.dumps(payload,ensure_ascii=False)))
        matched,_=core.plan(rows,lua.globals().catalog)
        core.apply(matched)

    # A metadata-only row is valid, and empty strings intentionally clear fields.
    apply({'ai_metadata':{
        'selection_reason':'表情自然',
        'clarity_status':'清晰度待确认',
        'clarity_reason':'眼部可能轻微虚焦',
        'review_items':'检查双眼',
    }})
    apply({'ai_metadata':{'clarity_reason':''}})
    photo=lua.globals().photo
    assert photo['properties']['selection_reason']=='表情自然'
    assert photo['properties']['clarity_status']=='清晰度待确认'
    assert photo['properties']['clarity_reason']==''
    assert photo['properties']['review_items']=='检查双眼'
    assert photo['caption']=='原说明' and photo['title']=='原标题'
    assert len(lua.globals().rawWrites)==0

    # Legacy rows never touch existing plug-in metadata.
    writes_before=len(lua.globals().pluginWrites)
    apply({'rating':5})
    assert len(lua.globals().pluginWrites)==writes_before
    assert photo['properties']['selection_reason']=='表情自然'

    invalid_metadata=[
        {},
        {'unknown':'x'},
        {'selection_reason':False},
        {'selection_reason':3},
    ]
    for metadata in invalid_metadata:
        payload={'format':'photo-cull-assistant','version':1,'photos':[
            {'path':'/photos/a.RW2','ai_metadata':metadata}
        ]}
        with pytest.raises(Exception):
            core.validate(decoder.decode(json.dumps(payload)))

    provider=lua.execute((PLUGIN/'MetadataProvider.lua').read_text('utf-8'))
    assert provider['schemaVersion']==1
    fields=list(provider['metadataFieldsForPhotos'].values())
    assert [field['id'] for field in fields]==list(core['aiMetadataFields'].values())
    assert all(field['dataType']=='string' for field in fields)
    assert all(field['readOnly'] is True and field['searchable'] is True for field in fields)
    assert [field['title'] for field in fields]==[
        'AI 选片理由','清晰度状态','清晰度核查理由','待确认事项'
    ]

    tagset=lua.execute((PLUGIN/'MetadataTagset.lua').read_text('utf-8'))
    assert tagset['title']=='AI选片助手'
    assert tagset['id']=='AIPhotoCullAssistant'
    tagset_items=list(tagset['items'].values())
    qualified=[]
    for item in tagset_items:
        if isinstance(item,str):
            value=item
        else:
            value=item[1]
        if value.startswith('com.photocullassistant.catalogimport.'):
            qualified.append(value)
    assert qualified==[
        'com.photocullassistant.catalogimport.selection_reason',
        'com.photocullassistant.catalogimport.clarity_status',
        'com.photocullassistant.catalogimport.clarity_reason',
        'com.photocullassistant.catalogimport.review_items',
    ]


@pytest.mark.parametrize('answer,expected', [('ok',5),('cancel',2)])
def test_lua_entrypoint_confirmation_and_catalog_gate(answer,expected):
    from lupa.lua51 import LuaRuntime
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().read_file=lambda path: ((PLUGIN/path.rsplit('/',1)[-1]).read_text('utf-8') if not path.endswith('results.json') else json.dumps({'format':'photo-cull-assistant','version':1,'export_id':'review-export-1','photos':[{'path':'/照片/a.RW2','rating':5}]}))
    lua.globals().answer=answer
    lua.execute('''
        _PLUGIN={path='/中文插件/PhotoCullAssistant.lrplugin'}
        photo={rating=2,pickStatus=1}
        function photo:getRawMetadata(k) return self[k] end
        function photo:setRawMetadata(k,v) assert(inGate); self[k]=v end
        catalog={}
        function catalog:findPhotoByPath(path) if path=='/照片/a.RW2' then return photo end end
        function catalog:getPath() return '/catalog.lrcat' end
        function catalog:setPropertyForPlugin() error('obsolete report write') end
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
    info=lua.execute((PLUGIN/'Info.lua').read_text('utf-8'))
    assert len(info['LrLibraryMenuItems'])==1
    assert info['LrLibraryMenuItems'][1]['file']=='ImportResults.lua'


def test_focus_keyword_add_remove_and_legacy_preserves_other_metadata():
    from lupa.lua51 import LuaRuntime
    lua=LuaRuntime(unpack_returned_tuples=True)
    core=lua.execute((PLUGIN/'Core.lua').read_text('utf-8'))
    decoder=lua.execute((PLUGIN/'json.lua').read_text('utf-8'))
    lua.execute('''
        keyword={getName=function() return 'AI选片_清晰度待确认' end}
        photo={rating=4,pickStatus=1,keywords={travel=true}}
        function photo:getRawMetadata(k) return self[k] end
        function photo:setRawMetadata(k,v) self[k]=v end
        function photo:addKeyword(k) self.keywords[k:getName()]=true end
        function photo:removeKeyword(k) self.keywords[k:getName()]=nil end
        catalog={}
        function catalog:findPhotoByPath(p) return photo end
        function catalog:createKeyword(name,synonyms,export,parent,reuse)
            assert(export==false and reuse==true); return keyword
        end
        function catalog:getKeywords() return {keyword} end
        function catalog:createSmartCollection(name,rule,parent,reuse)
            assert(rule[1].criteria=='keywords' and rule[1].value==name and reuse==true)
            collection=name
        end
    ''')
    def apply(row):
        payload={'format':'photo-cull-assistant','version':1,'photos':[{'path':'/photos/a.RW2',**row}]}
        rows=core.validate(decoder.decode(json.dumps(payload)))
        matched,_=core.plan(rows,lua.globals().catalog)
        keyword=core.prepareFocus(lua.globals().catalog,matched)
        core.apply(matched,keyword)
    apply({'focus_review':True});apply({'focus_review':True})
    photo=lua.globals().photo
    assert photo['keywords']['AI选片_清晰度待确认'] is True
    assert lua.globals().collection=='AI选片_清晰度待确认'
    assert photo['rating']==4 and photo['pickStatus']==1
    apply({'rating':5})
    assert photo['keywords']['AI选片_清晰度待确认'] is True
    apply({'focus_review':False})
    assert photo['keywords']['AI选片_清晰度待确认'] is None
    assert photo['keywords']['travel'] is True and photo['rating']==5 and photo['pickStatus']==1
    with pytest.raises(Exception):apply({'focus_review':'true'})
