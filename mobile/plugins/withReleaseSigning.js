/**
 * Подпись release-сборки своим ключом. android/ генерируется prebuild и не
 * хранится в git, поэтому правка build.gradle живёт здесь.
 *
 * Реквизиты — вне репозитория: ~/keystores/ozonsorter-release.credentials
 * (storeFile, storePassword, keyAlias, keyPassword); путь можно задать
 * -PozonCredentials=... Без них release не собирается: шаблон Expo по умолчанию
 * молча подписал бы его debug-ключом, и обновление поверх стало бы невозможным.
 */
const { withAppBuildGradle } = require('expo/config-plugins');

const RELEASE_SIGNING = `
        release {
            def credPath = findProperty('ozonCredentials') ?:
                "\${System.getProperty('user.home')}/keystores/ozonsorter-release.credentials"
            def credFile = file(credPath)
            if (credFile.exists()) {
                def props = new Properties()
                credFile.withInputStream { props.load(it) }
                storeFile file(props['storeFile'])
                storePassword props['storePassword']
                keyAlias props['keyAlias']
                keyPassword props['keyPassword']
            }
        }`;

const RELEASE_GUARD = `
gradle.taskGraph.whenReady { graph ->
    def credPath = findProperty('ozonCredentials') ?:
        "\${System.getProperty('user.home')}/keystores/ozonsorter-release.credentials"
    if (graph.allTasks.any { it.name.toLowerCase().contains('release') } && !file(credPath).exists()) {
        throw new GradleException("Не найден \${credPath}: release подписывать нечем (ключ — в репозитории KEYS и в Obsidian Secrets).")
    }
}
`;

module.exports = function withReleaseSigning(config) {
  return withAppBuildGradle(config, (cfg) => {
    let gradle = cfg.modResults.contents;
    if (gradle.includes('ozonsorter-release.credentials')) return cfg;
    const debugBlock = /(signingConfigs \{\s*debug \{[^}]*\})/;
    if (!debugBlock.test(gradle)) throw new Error('withReleaseSigning: signingConfigs.debug not found');
    gradle = gradle.replace(debugBlock, `$1${RELEASE_SIGNING}`);
    const releaseType = /(buildTypes \{[\s\S]*?release \{[\s\S]*?)signingConfig signingConfigs\.debug/;
    if (!releaseType.test(gradle)) throw new Error('withReleaseSigning: release buildType not found');
    gradle = gradle.replace(releaseType, '$1signingConfig signingConfigs.release');
    cfg.modResults.contents = `${gradle}\n${RELEASE_GUARD}`;
    return cfg;
  });
};
