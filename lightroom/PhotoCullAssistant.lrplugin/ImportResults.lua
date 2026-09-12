local Tasks=import 'LrTasks'
local Dialogs=import 'LrDialogs'
local Application=import 'LrApplication'
local Paths=import 'LrPathUtils'
local Files=import 'LrFileUtils'
-- SDK reads keep Chinese plugin paths working on Windows.
local json=assert(loadstring(Files.readFile(Paths.child(_PLUGIN.path,'json.lua'))))()
local Core=assert(loadstring(Files.readFile(Paths.child(_PLUGIN.path,'Core.lua'))))()

Tasks.startAsyncTask(function()
    local ok, err=Tasks.pcall(function()
        local selected=Dialogs.runOpenPanel { title='选择 AI选片助手 lightroom_results.json',
            canChooseFiles=true, canChooseDirectories=false, allowsMultipleSelection=false, fileTypes={'json'} }
        if not selected then return end
        local path=selected[1]
        local text=Files.readFile(path)
        assert(text and #text<=20*1024*1024, '结果文件无法读取或超过 20 MB')
        text=text:gsub('^\239\187\191','')
        local payload=json.decode(text)
        local rows=Core.validate(payload)
        local catalog=Application.activeCatalog()
        local matched, missing, changes=Core.plan(rows,catalog)
        local report={ catalog=catalog:getPath(), source=path, export_id=payload.export_id, status='not_applied', changes=changes, missing=missing }
        local reportStatus=catalog:withPrivateWriteAccessDo(function()
            catalog:setPropertyForPlugin(_PLUGIN, 'lastImportReport', json.encode(report))
        end,{timeout=30})
        assert(reportStatus=='executed', '未能保存导入报告，请稍后重试')
        if #matched==0 then
            Dialogs.message('没有可应用的照片', '请先把原照片导入当前 LR 目录，确保完整路径一致。', 'info')
            return
        end
        local answer=Dialogs.confirm('导入 AI选片助手结果',
            '匹配 '..#matched..' 张；未匹配 '..#missing..' 张。\n将更新结果中指定的星级、标记、清晰度状态和 AI 分析信息，未指定字段保持不变。\n导入后在图库的“元数据”面板下拉菜单选择“AI选片助手”，即可查看所选照片的分析信息。\n插件不写标题、说明或 XMP；若希望照片目录无 XMP，请关闭 LR 的“自动将更改写入 XMP”。',
            '应用到当前目录','取消')
        if answer~='ok' then return end
        local focusKeyword
        local prepared=catalog:withWriteAccessDo('准备清晰度待确认标记',function()
            focusKeyword=Core.prepareFocus(catalog,matched)
        end,{timeout=30})
        assert(prepared=='executed', '未能准备清晰度标记，请稍后重试')
        local status=catalog:withWriteAccessDo('导入 AI选片助手结果',function()
            Core.apply(matched,focusKeyword)
            report.status='applied'
            catalog:setPropertyForPlugin(_PLUGIN, 'lastImportReport', json.encode(report))
        end,{timeout=30})
        assert(status=='executed', '未取得 Lightroom 目录写入权限，请稍后重试')
        Dialogs.message('导入完成', '已更新 '..#matched..' 张；跳过 '..#missing..' 张。\n在图库“元数据”面板下拉菜单选择“AI选片助手”，可查看当前所选照片的分析信息。\n清晰度待确认照片也可在同名智能收藏夹中查看。', 'info')
    end)
    if not ok then Dialogs.message('导入失败', tostring(err), 'critical') end
end)
