#include "i18n.h"
#include <QCoreApplication>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocale>
#include <QSettings>
#include <QTranslator>

static void languageResources() { static const bool once = [] { Q_INIT_RESOURCE(i18n); return true; }(); Q_UNUSED(once); }
namespace cdng {
namespace {
QString selected = "en", preference = "system";
class Catalog : public QTranslator {
    QJsonObject values_;
public:
    Catalog() { languageResources(); QFile file(":/i18n/es.json"); if (file.open(QIODevice::ReadOnly)) values_ = QJsonDocument::fromJson(file.readAll()).object(); }
    bool isEmpty() const override { return values_.isEmpty(); }
    QString translate(const char *, const char *source, const char *, int) const override {
        return selected == "es" ? values_.value(QString::fromUtf8(source)).toString() : QString();
    }
};
}
QString languageFor(const QString &choice, const QStringList &systemUiLanguages) {
    if (choice == "en" || choice == "es") return choice;
    if (systemUiLanguages.isEmpty()) return "en";
    const auto primary = systemUiLanguages.first().section('-', 0, 0).section('_', 0, 0).toLower();
    return primary == "es" ? "es" : "en";
}
void initializeLanguage(QString choice, bool persist) {
    if (choice.isEmpty()) choice = QSettings("elcacharrista", "cdng2braw").value("ui/language", "system").toString();
    preference = choice == "en" || choice == "es" ? choice : QString("system");
    selected = languageFor(preference, QLocale::system().uiLanguages());
    if (persist) { QSettings settings("elcacharrista", "cdng2braw"); settings.setValue("ui/language", preference); settings.sync(); }
    static Catalog catalog;
    static QTranslator base;
    static const bool loaded = base.load(":/i18n/qtbase_es.qm"); Q_UNUSED(loaded);
    QCoreApplication::removeTranslator(&catalog); QCoreApplication::removeTranslator(&base);
    if (selected == "es") { QCoreApplication::installTranslator(&base); QCoreApplication::installTranslator(&catalog); }
}
QString currentLanguage() { return selected; }
QString languagePreference() { return preference; }
QString t(const char *source) { return QCoreApplication::translate("CDNG", source); }
}
