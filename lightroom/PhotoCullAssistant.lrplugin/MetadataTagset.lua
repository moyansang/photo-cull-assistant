local pluginId = 'com.photocullassistant.catalogimport'

return {
    title = 'AI选片助手',
    id = 'AIPhotoCullAssistant',
    items = {
        'com.adobe.filename',
        'com.adobe.rating',
        'com.adobe.separator',
        { pluginId..'.selection_reason', height_in_lines=4 },
        pluginId..'.clarity_status',
        { pluginId..'.clarity_reason', height_in_lines=3 },
        { pluginId..'.review_items', height_in_lines=4 },
    },
}
