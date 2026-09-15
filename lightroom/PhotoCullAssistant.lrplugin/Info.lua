return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.photocullassistant.catalogimport',
    LrPluginName = 'AI选片助手',
    VERSION = { major=1, minor=5, revision=0, build=0 },
    LrMetadataProvider = 'MetadataProvider.lua',
    LrMetadataTagsetFactory = { 'MetadataTagset.lua' },
    LrLibraryMenuItems = {
        { title='导入 AI选片助手结果', file='ImportResults.lua' },
    },
}
