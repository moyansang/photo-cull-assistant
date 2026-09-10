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
            Dialogs.message('没有可应用的照片', '请先把原照片导入当前 LR 目录，确保完整路径一致。\n可在插件菜单“查看上次导入报告”查看未匹配路径。', 'info')
            return
        end
        local answer=Dialogs.confirm('导入 AI选片助手结果',
            '匹配 '..#matched..' 张；未匹配 '..#missing..' 张。\n将更新结果中指定的星级/标记，其他字段保持不变。\n插件不写 XMP；若希望照片目录无 XMP，请关闭 LR 的“自动将更改写入 XMP”。\n原有星级和标记已保存，可在插件菜单查看导入报告。',
            '应用到当前目录','取消')
        if answer~='ok' then return end
        local status=catalog:withWriteAccessDo('导入 AI选片助手结果',function()
            Core.apply(matched)
            report.status='applied'
            catalog:setPropertyForPlugin(_PLUGIN, 'lastImportReport', json.encode(report))
        end,{timeout=30})
        assert(status=='executed', '未取得 Lightroom 目录写入权限，请稍后重试')
        Dialogs.message('导入完成', '已更新 '..#matched..' 张；跳过 '..#missing..' 张。\n在插件菜单“查看上次导入报告”可查看具体路径及变更前状态。', 'info')
    end)
    if not ok then Dialogs.message('导入失败', tostring(err), 'critical') end
end)
