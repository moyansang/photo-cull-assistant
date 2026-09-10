local M = {}
function M.validate(data)
    assert(type(data)=='table' and data.format=='photo-cull-assistant' and data.version==1, '结果文件格式或版本不支持')
    assert(type(data.photos)=='table', '缺少 photos 列表')
    local seen, count = {}, 0
    for k in pairs(data.photos) do
        assert(type(k)=='number' and k>=1 and k%1==0, 'photos 必须为数组')
        count=count+1
    end
    assert(count==#data.photos, 'photos 数组不连续')
    for _, row in ipairs(data.photos) do
        assert(type(row)=='table' and type(row.path)=='string', '缺少照片完整路径')
        local path=row.path:gsub('\\','/')
        assert(path:match('^%a:/') or path:sub(1,1)=='/', '必须使用照片绝对路径')
        assert(not path:find('%z') and not path:match('/%.%./'), '无效照片路径')
        local key=path:lower()
        assert(not seen[key], '重复照片路径：'..row.path)
        seen[key]=true
        assert(row.rating~=nil or row.pick_status~=nil, '缺少评级或标记')
        if row.rating~=nil then
            assert(type(row.rating)=='number' and row.rating%1==0 and row.rating>=1 and row.rating<=5, '无效星级')
        end
        if row.pick_status~=nil then
            assert(row.pick_status==-1 or row.pick_status==0 or row.pick_status==1, '无效留用/弃置标记')
        end
    end
    return data.photos
end

function M.plan(rows, catalog)
    local matched, missing, report = {}, {}, {}
    for _, row in ipairs(rows) do
        local photo=catalog:findPhotoByPath(row.path)
        if photo and not photo:getRawMetadata('isVirtualCopy') then
            table.insert(matched, { photo=photo, row=row })
            table.insert(report, { path=row.path, before_rating=photo:getRawMetadata('rating') or 0,
                before_pick_status=photo:getRawMetadata('pickStatus') or 0,
                requested_rating=row.rating, requested_pick_status=row.pick_status })
        else
            table.insert(missing, row.path)
        end
    end
    return matched, missing, report
end

function M.apply(matched)
    for _, item in ipairs(matched) do
        if item.row.rating~=nil then item.photo:setRawMetadata('rating', item.row.rating) end
        if item.row.pick_status~=nil then item.photo:setRawMetadata('pickStatus', item.row.pick_status) end
    end
end
return M
