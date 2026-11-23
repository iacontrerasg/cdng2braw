#include "i18n.h"
#include <QLocale>
#include <QSettings>
#include <QTemporaryDir>
#include <QtTest>
using namespace cdng;
class I18nTests : public QObject {
    Q_OBJECT
private slots:
    void systemLanguageUsesPrimaryUiLanguageAndEnglishFallback() {
        QCOMPARE(languageFor("system", {"es-ES", "en-US"}), QString("es"));
        QCOMPARE(languageFor("system", {"es-MX"}), QString("es"));
        QCOMPARE(languageFor("system", {"en-ES", "es-ES"}), QString("en"));
        QCOMPARE(languageFor("system", {"fr-FR", "es-ES"}), QString("en"));
        QCOMPARE(languageFor("system", {}), QString("en"));
        QCOMPARE(languageFor("en", {"es-ES"}), QString("en"));
        QCOMPARE(languageFor("es", {"en-US"}), QString("es"));
    }
    void preferenceIsPersistedWithoutChangingLanguageFallback() {
        QTemporaryDir temp;
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, temp.path());
        initializeLanguage("es", true); QCOMPARE(t("Convert"), QString("Convertir"));
        initializeLanguage(); QCOMPARE(currentLanguage(), QString("es"));
        initializeLanguage("en", true); QCOMPARE(t("Convert"), QString("Convert"));
        initializeLanguage(); QCOMPARE(currentLanguage(), QString("en"));
        initializeLanguage("system", true);
        QCOMPARE(currentLanguage(), languageFor("system", QLocale::system().uiLanguages()));
    }
};
QTEST_GUILESS_MAIN(I18nTests)
#include "i18n_test.moc"
