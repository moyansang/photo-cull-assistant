local Tasks=import 'LrTasks'
local Dialogs=import 'LrDialogs'
local View=import 'LrView'
local Application=import 'LrApplication'
Tasks.startAsyncTask(function()
    local text=Application.activeCatalog():getPropertyForPlugin(_PLUGIN,'lastImportReport')
    if not text then Dialogs.message('导入报告','当前目录尚无导入记录。','info'); return end
    local factory=View.osFactory()
    Dialogs.presentModalDialog { title='上次导入报告（可复制）', actionVerb='关闭',
        contents=factory:column {
            factory:static_text { title='包含未匹配路径、变更前状态及应用状态。只保留最近一次导入记录。' },
            factory:edit_field { value=text, width_in_chars=90, height_in_lines=26 },
        },
    }
end)
